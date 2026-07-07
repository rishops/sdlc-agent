---
name: test-authoring
description: Write and run tests that actually verify a change against its acceptance criteria, then report a structured result. Use when verifying an implemented change in the sandbox.
---

# Test Authoring

You prove the change works by writing tests tied to the acceptance criteria and
running the suite — honestly.

## Procedure

1. **Target the acceptance criteria.** Add or extend tests that assert the
   specific behavior the issue requires (e.g. "GET /health returns 200"), using
   the repo's existing test framework and layout.
2. **Match the stack.** Detect the framework from the repo context
   (pytest / unittest, jest / vitest, go test, JUnit, …). If there is genuinely
   no test setup, add the minimal idiomatic one for that stack.
3. **Run the suite** with `run_in_repo` using the repo's test command (or the
   minimal one you added). Capture the real pass/fail counts and failing ids.
4. **Report honestly.** Call `record_test_report` with the true result. Set
   `passed=true` ONLY if the suite actually passed. Never fake a green run.

## Constraints

- Do not modify application code to make a test pass — that's the Coder's job on
  the next loop iteration; just report failures accurately.
- Keep test runs bounded; don't kick off long-running services.

## Output

Your final action is a single `record_test_report` call.
