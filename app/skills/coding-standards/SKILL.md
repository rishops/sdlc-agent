---
name: coding-standards
description: Implement a change so it blends into the target repository — match its conventions, style, and structure rather than imposing new ones. Use when editing files to satisfy a change plan.
---

# Coding Standards

You implement changes that look like they were written by the repo's own
maintainers. The repo context (languages, build system, conventions) is your
style guide.

## Procedure

1. **Read before you write.** Open the file you intend to change with
   `read_repo_file` and mirror its existing imports, naming, indentation, error
   handling, and structure. Never overwrite a file blind.
2. **Stay in scope.** Implement only what the change plan calls for. Do not
   refactor, reformat, or "improve" unrelated code.
3. **Match the stack.** Use the libraries/patterns already present (e.g. the
   repo's web framework, its logging, its config approach) — don't introduce a
   new dependency unless the plan explicitly requires it (and flag it if so).
4. **Keep edits minimal and reviewable.** Prefer the smallest diff that satisfies
   the acceptance criteria.

## Output

Apply each edit with `write_repo_file` (full new file content). Do not run the
test suite — the Verifier does that.
