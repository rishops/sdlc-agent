"""Sandbox abstraction selected by the ``SANDBOX_BACKEND`` env flag.

Two backends, one interface (TDD section 7):

* ``host``         — runs git/build/test on the local machine. Offline, no GCP.
                     Code execution uses :class:`UnsafeLocalCodeExecutor`. This
                     is the backend used for P1 local verification.
* ``agent_engine`` — runs in a managed GCP sandbox via
                     :class:`AgentEngineSandboxCodeExecutor`. Used at deploy time.

Two distinct surfaces are exposed:

1. :func:`get_code_executor` — the ADK ``code_executor`` for agents that run
   model-emitted code (the Coder/Tester build loop, added in a later milestone).
   Both backends are real and selected purely by the flag.
2. The repo-recon **FunctionTools** (:data:`SANDBOX_TOOLS`) — deterministic
   git/clone/scan helpers used by RepoContext in P1. These keep large file
   content out of the model context by returning capped, structured results.

Security note: with ``SANDBOX_BACKEND=host`` the recon tools and code executor
run commands on the local machine (equivalent to ``UnsafeLocalCodeExecutor``).
That is intentional for local development; production uses ``agent_engine``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from google.adk.code_executors import (
    BaseCodeExecutor,
    UnsafeLocalCodeExecutor,
)
from google.adk.tools import ToolContext

from app import config

# Session-state slot holding the active working directory for the run.
_WORKDIR_KEY = "temp:repo_workdir"
# Repo-relative paths the agent deliberately wrote via write_repo_file. Only
# these are staged/committed, so build artifacts (e.g. __pycache__/*.pyc created
# when the Tester runs the suite) never pollute the diff or the PR commit.
_WRITTEN_PATHS_KEY = "temp:written_paths"

# Caps that keep tool output out of the model's reasoning context.
_MAX_TREE_ENTRIES = 400
_MAX_FILE_BYTES = 60_000
_MAX_OUTPUT_CHARS = 8_000

_SECRET_RE = re.compile(r"(x-access-token:|ghp_|github_pat_)[^@/\s]+", re.IGNORECASE)


def _redact(text: str) -> str:
    return _SECRET_RE.sub("***", text)


# --------------------------------------------------------------------------
# Code executor (build loop) — both backends, selected by flag
# --------------------------------------------------------------------------
def get_code_executor() -> BaseCodeExecutor:
    """Return the code executor for the configured backend.

    Used as ``Agent(code_executor=get_code_executor())`` by code-running agents.
    """
    if config.SANDBOX_BACKEND == "agent_engine":
        from google.adk.code_executors.agent_engine_sandbox_code_executor import (
            AgentEngineSandboxCodeExecutor,
        )

        if not (config.SANDBOX_AGENT_ENGINE_RESOURCE or config.SANDBOX_RESOURCE_NAME):
            raise RuntimeError(
                "SANDBOX_BACKEND=agent_engine requires SANDBOX_AGENT_ENGINE_RESOURCE "
                "or SANDBOX_RESOURCE_NAME to be set in your .env."
            )
        return AgentEngineSandboxCodeExecutor(
            agent_engine_resource_name=config.SANDBOX_AGENT_ENGINE_RESOURCE or None,
            sandbox_resource_name=config.SANDBOX_RESOURCE_NAME or None,
        )
    # Default: local execution, fully offline.
    return UnsafeLocalCodeExecutor()


def maybe_code_executor() -> BaseCodeExecutor | None:
    """Code executor for the build loop, or None on the host backend.

    On ``host`` the Coder/Tester drive edits and test runs through the
    deterministic EDIT_TOOLS (no executor needed). On ``agent_engine`` the work
    runs inside the managed sandbox via the code executor.
    """
    if config.SANDBOX_BACKEND == "agent_engine":
        return get_code_executor()
    return None


# --------------------------------------------------------------------------
# Repo-recon FunctionTools (host implementation; used by P1)
# --------------------------------------------------------------------------
# These helpers return a recoverable error *dict* (never raise) so a tool can
# hand the problem back to the model instead of crashing the whole run. A raised
# exception in a FunctionTool aborts the event stream; an error dict lets the
# model self-correct (e.g. call clone_repo, then retry).
def _host_backend_error() -> dict | None:
    if config.SANDBOX_BACKEND != "host":
        return {
            "status": "error",
            "error": (
                "The repo-recon tools require SANDBOX_BACKEND=host. With "
                "agent_engine, repo recon runs inside the managed sandbox via the "
                "code executor (deploy milestone)."
            ),
        }
    return None


def _workdir(tool_context: ToolContext) -> Path | None:
    path = tool_context.state.get(_WORKDIR_KEY)
    return Path(path) if path else None


def _track_written(tool_context: ToolContext, path: str) -> None:
    paths = list(tool_context.state.get(_WRITTEN_PATHS_KEY, []) or [])
    if path not in paths:
        paths.append(path)
    tool_context.state[_WRITTEN_PATHS_KEY] = paths


def _stage_written(root: Path, tool_context: ToolContext) -> list[str]:
    """Stage ONLY the agent-written paths (never `git add -A`).

    Returns the list of staged paths. Keeps untracked build artifacts
    (__pycache__, *.pyc, coverage files) out of the diff and the commit.
    """
    paths = list(tool_context.state.get(_WRITTEN_PATHS_KEY, []) or [])
    if paths:
        subprocess.run(
            ["git", "add", "--", *paths],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=60,
        )
    return paths


_NO_CLONE_ERROR = {
    "status": "error",
    "error": "No repository cloned yet — call clone_repo first, then retry.",
}


def clone_repo(repo: str, branch: str, tool_context: ToolContext) -> dict:
    """Shallow-clone a GitHub repository into a fresh local working directory.

    Args:
        repo: Target repository as 'owner/name'.
        branch: Branch to clone. Pass an empty string for the default branch.

    Returns:
        dict with 'status', 'workdir', and 'branch'.
    """
    if err := _host_backend_error():
        return err
    try:
        token = config.github_token()
    except RuntimeError as e:
        return {"status": "error", "error": str(e)}
    root = config.HOST_WORKDIR_ROOT or tempfile.gettempdir()
    os.makedirs(root, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="sdlc-agent-", dir=root)

    url = f"https://x-access-token:{token}@github.com/{repo}.git"
    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [url, workdir]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        shutil.rmtree(workdir, ignore_errors=True)
        return {
            "status": "error",
            "error": _redact(proc.stderr.strip())[:_MAX_OUTPUT_CHARS],
        }

    tool_context.state[_WORKDIR_KEY] = workdir
    return {"status": "success", "workdir": workdir, "branch": branch or "(default)"}


def list_repo_tree(max_entries: int, tool_context: ToolContext) -> dict:
    """List files in the cloned repository (excluding .git).

    Args:
        max_entries: Maximum number of paths to return (hard-capped).

    Returns:
        dict with 'status', 'count', 'truncated', and 'paths' (repo-relative).
    """
    if err := _host_backend_error():
        return err
    root = _workdir(tool_context)
    if root is None:
        return _NO_CLONE_ERROR
    cap = min(max_entries, _MAX_TREE_ENTRIES) if max_entries > 0 else _MAX_TREE_ENTRIES
    paths: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        if ".git" in dirnames:
            dirnames.remove(".git")
        for name in filenames:
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            paths.append(rel)
            if len(paths) >= cap:
                return {"status": "success", "count": len(paths), "truncated": True, "paths": paths}
    return {"status": "success", "count": len(paths), "truncated": False, "paths": paths}


def read_repo_file(path: str, tool_context: ToolContext) -> dict:
    """Read a text file from the cloned repository (size-capped).

    Args:
        path: Repo-relative path to the file.

    Returns:
        dict with 'status' and 'content' (truncated if large).
    """
    if err := _host_backend_error():
        return err
    root = _workdir(tool_context)
    if root is None:
        return _NO_CLONE_ERROR
    target = (root / path).resolve()
    if not str(target).startswith(str(root.resolve())):
        return {"status": "error", "error": "Path escapes the repository."}
    if not target.is_file():
        return {"status": "error", "error": f"Not a file: {path}"}
    data = target.read_bytes()[:_MAX_FILE_BYTES]
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"status": "error", "error": "Binary or non-UTF8 file."}
    truncated = target.stat().st_size > _MAX_FILE_BYTES
    return {"status": "success", "content": content, "truncated": truncated}


def run_in_repo(command: str, tool_context: ToolContext) -> dict:
    """Run a shell command inside the cloned repo (host backend; results capped).

    Args:
        command: The shell command to execute (e.g. a build or test command).

    Returns:
        dict with 'status', 'returncode', 'stdout', and 'stderr' (truncated).
    """
    if err := _host_backend_error():
        return err
    root = _workdir(tool_context)
    if root is None:
        return _NO_CLONE_ERROR
    proc = subprocess.run(
        command,
        shell=True,
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=config.MAX_RUN_WALL_SECONDS,
    )
    return {
        "status": "success" if proc.returncode == 0 else "nonzero_exit",
        "returncode": proc.returncode,
        "stdout": _redact(proc.stdout)[-_MAX_OUTPUT_CHARS:],
        "stderr": _redact(proc.stderr)[-_MAX_OUTPUT_CHARS:],
    }


def write_repo_file(path: str, content: str, tool_context: ToolContext) -> dict:
    """Create or overwrite a text file in the cloned repository (host backend).

    Args:
        path: Repo-relative path to write.
        content: Full new text content of the file.

    Returns:
        dict with 'status' and 'bytes_written'.
    """
    if err := _host_backend_error():
        return err
    root = _workdir(tool_context)
    if root is None:
        return _NO_CLONE_ERROR
    target = (root / path).resolve()
    if not str(target).startswith(str(root.resolve())):
        return {"status": "error", "error": "Path escapes the repository."}
    target.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8")
    target.write_bytes(data)
    _track_written(tool_context, path)
    return {"status": "success", "bytes_written": len(data), "path": path}


def repo_diff(tool_context: ToolContext) -> dict:
    """Return the current `git diff` of the working tree (truncated).

    Returns:
        dict with 'status' and 'diff'.
    """
    if err := _host_backend_error():
        return err
    root = _workdir(tool_context)
    if root is None:
        return _NO_CLONE_ERROR
    staged = _stage_written(root, tool_context)
    proc = subprocess.run(
        ["git", "diff", "--cached", "--", *staged] if staged else ["git", "diff", "--cached"],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=60,
    )
    return {
        "status": "success",
        "files": staged,
        "diff": _redact(proc.stdout)[: _MAX_OUTPUT_CHARS * 2],
    }


# Read/recon tools attached to RepoContext.
SANDBOX_TOOLS = [clone_repo, list_repo_tree, read_repo_file, run_in_repo]

# Edit tools attached to the Coder/Tester build loop (read + write + run).
EDIT_TOOLS = [read_repo_file, list_repo_tree, write_repo_file, run_in_repo, repo_diff]


# --------------------------------------------------------------------------
# Git tools (Delivery) — branch, commit, push the host working tree
# --------------------------------------------------------------------------
def create_branch(name: str, tool_context: ToolContext) -> dict:
    """Create and switch to a new branch in the cloned repo.

    Args:
        name: The new branch name (e.g. 'agent/issue-12-health-endpoint').

    Returns:
        dict with 'status' and 'branch'.
    """
    if err := _host_backend_error():
        return err
    root = _workdir(tool_context)
    if root is None:
        return _NO_CLONE_ERROR
    proc = subprocess.run(
        ["git", "checkout", "-b", name],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        return {"status": "error", "error": _redact(proc.stderr.strip())[:_MAX_OUTPUT_CHARS]}
    return {"status": "success", "branch": name}


def commit_all(message: str, tool_context: ToolContext) -> dict:
    """Stage the agent-written files and commit them with the agent's identity.

    Only files written via ``write_repo_file`` are staged — build artifacts and
    other untracked files are never committed.

    Args:
        message: The commit message.

    Returns:
        dict with 'status'.
    """
    if err := _host_backend_error():
        return err
    root = _workdir(tool_context)
    if root is None:
        return _NO_CLONE_ERROR
    staged = _stage_written(root, tool_context)
    if not staged:
        return {"status": "error", "error": "Nothing to commit — no files were written via write_repo_file."}
    proc = subprocess.run(
        [
            "git",
            "-c", f"user.name={config.GIT_AUTHOR_NAME}",
            "-c", f"user.email={config.GIT_AUTHOR_EMAIL}",
            "commit", "-m", message,
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        out = (proc.stdout + proc.stderr).strip()
        if "nothing to commit" in out:
            return {"status": "error", "error": "Nothing to commit — no changes were made."}
        return {"status": "error", "error": _redact(out)[:_MAX_OUTPUT_CHARS]}
    return {"status": "success"}


def push_branch(name: str, tool_context: ToolContext) -> dict:
    """Push a branch to origin (the token remote set at clone time).

    Args:
        name: The branch to push.

    Returns:
        dict with 'status'.
    """
    if err := _host_backend_error():
        return err
    root = _workdir(tool_context)
    if root is None:
        return _NO_CLONE_ERROR
    proc = subprocess.run(
        ["git", "push", "-u", "origin", name],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=180,
    )
    if proc.returncode != 0:
        return {"status": "error", "error": _redact(proc.stderr.strip())[:_MAX_OUTPUT_CHARS]}
    return {"status": "success", "branch": name}


# Git tools attached to Delivery.
GIT_TOOLS = [create_branch, commit_all, push_branch]
