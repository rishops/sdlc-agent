"""Unit tests for the host edit tools and the test-report recorder."""

from __future__ import annotations

import subprocess

from app import schemas
from app.tools import sandbox
from app.tools.state_tools import record_test_report


class _Ctx:
    def __init__(self, state: dict) -> None:
        self.state = state


def _init_git_repo(path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)


def test_write_repo_file_and_diff(tmp_path) -> None:
    _init_git_repo(tmp_path)
    ctx = _Ctx({"temp:repo_workdir": str(tmp_path)})

    res = sandbox.write_repo_file("pkg/main.py", "print('hi')\n", ctx)
    assert res["status"] == "success"
    assert (tmp_path / "pkg" / "main.py").read_text() == "print('hi')\n"

    diff = sandbox.repo_diff(ctx)
    assert diff["status"] == "success"
    assert "main.py" in diff["diff"]


def test_selective_staging_excludes_untracked_artifacts(tmp_path) -> None:
    _init_git_repo(tmp_path)
    ctx = _Ctx({"temp:repo_workdir": str(tmp_path)})

    # Agent writes a real source file via the tool (tracked)...
    sandbox.write_repo_file("main.py", "x = 1\n", ctx)
    # ...and a build artifact appears on disk WITHOUT going through the tool.
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "main.cpython-311.pyc").write_bytes(b"\x00\x01")

    diff = sandbox.repo_diff(ctx)
    assert diff["files"] == ["main.py"]
    assert "main.py" in diff["diff"]
    assert "__pycache__" not in diff["diff"]

    assert sandbox.commit_all("feat: add main", ctx)["status"] == "success"
    # The artifact must remain untracked (never committed).
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tmp_path, capture_output=True, text=True
    ).stdout
    assert "__pycache__" in status  # still present as untracked, not committed


def test_write_repo_file_rejects_path_escape(tmp_path) -> None:
    ctx = _Ctx({"temp:repo_workdir": str(tmp_path)})
    res = sandbox.write_repo_file("../escape.py", "x", ctx)
    assert res["status"] == "error"


def test_write_repo_file_requires_clone() -> None:
    res = sandbox.write_repo_file("a.py", "x", _Ctx({}))
    assert res["status"] == "error"
    assert "clone_repo" in res["error"]


def test_create_branch_and_commit(tmp_path) -> None:
    _init_git_repo(tmp_path)
    # need an initial commit so branching works cleanly
    (tmp_path / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t.com", "-c", "user.name=t", "commit", "-q", "-m", "seed"],
        cwd=tmp_path,
        check=True,
    )
    ctx = _Ctx({"temp:repo_workdir": str(tmp_path)})

    assert sandbox.create_branch("agent/test-1", ctx)["status"] == "success"
    sandbox.write_repo_file("f.py", "x=1\n", ctx)
    assert sandbox.commit_all("feat: add f", ctx)["status"] == "success"

    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=tmp_path, capture_output=True, text=True
    ).stdout.strip()
    assert branch == "agent/test-1"


def test_commit_all_reports_nothing_to_commit(tmp_path) -> None:
    _init_git_repo(tmp_path)
    ctx = _Ctx({"temp:repo_workdir": str(tmp_path)})
    res = sandbox.commit_all("empty", ctx)
    assert res["status"] == "error" and "Nothing to commit" in res["error"]


def test_record_test_report_writes_state() -> None:
    ctx = _Ctx({})
    res = record_test_report(
        passed=True, total=3, failed=[], logs_ref="pytest", coverage=-1, tool_context=ctx
    )
    assert res["status"] == "success" and res["passed"] is True
    stored = ctx.state[schemas.STATE_TEST_REPORT]
    assert stored["passed"] is True and stored["total"] == 3 and stored["coverage"] is None
