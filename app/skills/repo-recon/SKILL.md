---
name: repo-recon
description: Clone a repository into the sandbox and build a structured map of it — languages, build system, test command, conventions, and the files relevant to the issue. Use after the issue spec is known.
---

# Repo Recon

You build a `RepoContext` for the target repository so the Planner can work
without re-discovering the codebase. Keep large file contents out of your
reasoning — rely on the structured, capped tool results.

## Procedure

1. **Clone**: call `clone_repo(repo, branch)` with the repo from the issue spec.
   Pass an empty `branch` to get the default branch. If the clone fails, report
   the error rather than guessing the structure.
2. **Map the tree**: call `list_repo_tree` to see the layout. Identify the
   default branch and the primary languages from file extensions and root files.
3. **Detect build & test tooling** from manifest files, e.g.:
   - Python: `pyproject.toml` / `requirements.txt` → `pytest`, `uv`, `poetry`.
   - Node: `package.json` scripts → `npm test`, `pnpm`, `yarn`.
   - Go: `go.mod` → `go test ./...`. Java: `pom.xml`/`build.gradle` → `mvn test`.
   Read only the few manifest/config files you need with `read_repo_file`.
4. **Find relevant paths**: using the issue spec's `affected_area` and acceptance
   criteria, list the handful of files/dirs most likely to change. Read a couple
   to confirm — do not read the whole tree.
5. **Note conventions**: test layout, naming, formatting/lint config, module
   structure — anything the Coder must follow later.

## Constraints

- Do not run build/test commands during recon unless strictly necessary to
  discover the test command; heavy execution belongs in the build loop.
- Never read or echo secrets/`.env` files; summarise structure, not credentials.

## Output

Call the `record_repo_context` tool exactly once with the findings as the final
step.
