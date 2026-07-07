---
name: safe-edits
description: Make file edits safely — preserve unrelated code, avoid destructive actions, and never leak secrets. Use alongside coding-standards when modifying a repository.
---

# Safe Edits

Guardrails for editing a real repository you do not own.

## Rules

- **Preserve surrounding code.** `write_repo_file` overwrites the whole file, so
  always `read_repo_file` first and re-emit the full content with only the
  intended lines changed. Losing unrelated code is a failure.
- **No destructive operations** without explicit instruction: don't delete files,
  remove tests, drop dependencies, or rewrite history. If the plan requires a
  deletion, treat it as a risk to flag, not a default.
- **Never touch secrets.** Do not read, move, print, or commit `.env`, key files,
  or credentials. Do not embed tokens in code.
- **Don't fight the tests.** Fix the cause of a failing test; never weaken or
  delete a test to make the suite pass.
- **Stay within the working tree.** Only edit paths inside the cloned repo.

## When unsure

If an edit would be destructive, broaden scope, or change dependencies, prefer
the narrower action and record the concern (it belongs in the plan's risk flags).
