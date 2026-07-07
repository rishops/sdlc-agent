# DESIGN_SPEC.md

> The implementation contract for this agent. Derived from
> `technical-design-document.md` (TDD). When in doubt, this spec wins for
> *behavior*; the TDD remains the reference for the full target architecture.

## Overview

This project is an autonomous **issue-to-PR engineering workflow** built on
Google ADK 2.0. When a GitHub issue is labeled with a trigger label
(`agent:build`), a multi-agent pipeline understands the requirement, maps the
target repository, plans a change, (later) implements and tests it in a sandbox,
reviews it, and opens a pull request — with a human always merging.

The system is **local-first**. Cloud deployment (Agent Runtime, the
webhook→Pub/Sub→Dispatcher ingress, Memory Bank, governance) is deferred until
the agent is verified locally. The current milestone is **P1 — Read & Plan**.

### Milestone scope

| Milestone | Scope | Status |
|-----------|-------|--------|
| **P1 — Read & Plan** | Intake → RepoContext → Planner. Ack, clone, repo map, change-plan comment. | Done |
| **P2a — Approve & Build** | Agent **auto-approves the plan** (re-plan loop, replaces the human gate) → Coder⇄Tester loop edits + runs tests in the host sandbox until green. | Done |
| **P2b — Review & Deliver** | Reviewer records a verdict (gates delivery) → Delivery pushes a branch (git) + opens a real **draft PR** linked to the issue via write MCP. A human merges. | **Active** |
| P3 — Harden & deploy | Agent Runtime, Memory Bank, Agent Gateway, eval/online monitors, canary. | Deferred |

### Plan approval is automated (no human gate)
Per the user's decision, the TDD §9 pre-implementation HITL gate is replaced by a
**Plan Reviewer agent**. The Planner and Plan Reviewer run in a bounded loop
(`MAX_PLAN_ITERATIONS`): the reviewer emits a `PlanApproval`; on rejection the
planner revises using `required_changes`; the loop exits when `approved` is true.
If still unapproved at the budget, the run stops with `agent:plan-needs-revision`.
The pre-delivery gate stays human (draft PR; a human always merges).

## Pipeline (P1)

1. **Intake / Requirement Analyst** (`single_turn`): reads the issue via
   read-only GitHub MCP, applies the `spec-extraction` skill, records an
   `IssueSpec` (incl. an actionability judgement).
2. **Repo Context / Codebase Navigator** (`single_turn`): clones the repo into
   the sandbox, applies the `repo-recon` skill, records a `RepoContext`
   (languages, build system, test command, relevant paths, conventions).
3. **Planner / Architect** (`single_turn`): pure reasoning over the spec +
   context; emits a structured `ChangePlan` (`output_schema`).
4. **Orchestrator side-effects** (callbacks): posts the "🤖 picked up" ack +
   `agent:in-progress` at start; posts the formatted plan + `agent:plan-ready`
   at end (or `agent:needs-clarification` if the issue is not actionable).

## Example use cases

1. **Actionable bug** — Issue: "GET /health returns 500." → IssueSpec with
   acceptance criterion "GET /health returns 200"; RepoContext locating the
   health handler; ChangePlan naming the handler file + a regression test.
2. **Feature request** — Issue: "Add a `--json` flag to the CLI." → plan listing
   the arg-parser file, output formatting, and a test asserting JSON output.
3. **Non-actionable** — Issue: "It's broken, pls fix." → IssueSpec
   `is_actionable=false` with one clarifying question; orchestrator posts the
   question and sets `agent:needs-clarification`.

## Tools required

- **GitHub MCP** (remote, `https://api.githubcopilot.com/mcp/`): read-only
  toolset for analysis (issues/repos/labels/discussions/code_security/
  dependabot); write-scoped toolset reserved for Delivery (P2).
- **Sandbox** (`SANDBOX_BACKEND` flag): `host` (local git/build/test, offline)
  or `agent_engine` (managed GCP sandbox). Code execution uses
  `UnsafeLocalCodeExecutor` (host) / `AgentEngineSandboxCodeExecutor` (cloud).
- **GitHub status REST helper**: deterministic, orchestrator-owned ack/label/
  plan-comment writes (not routed through the LLM).

## Constraints & safety rules

- **Issue/PR/file text is untrusted data, never instructions.** Agents must not
  follow embedded directives that change scope, exfiltrate secrets, or widen
  access.
- **Least privilege:** read-only MCP everywhere except the single Delivery write
  path. A bad toolset string fails closed (HTTP 400).
- **No auto-merge.** Delivery opens a *draft* PR; a human always merges.
- **Secrets never enter model context or logs.** The GitHub token is passed only
  into MCP/REST headers; sandbox output is secret-redacted and size-capped.
- **Surgical edits** (P2 Coder): change only what the plan targets.
- **Budget bounds:** loop `max_iterations` and a wall-clock cap per run.

## Success criteria (P1)

- On a seeded/real issue in the test repo, the pipeline produces a **coherent,
  grounded `ChangePlan`** (references real files from `RepoContext`, concrete
  steps, explicit test strategy).
- Ack comment + `agent:in-progress` appear within seconds; the plan comment +
  `agent:plan-ready` appear at the end.
- The full run is traceable (events + state slots `issue_spec`, `repo_context`,
  `change_plan`) with **no GCP/cloud dependency** (`SANDBOX_BACKEND=host`).
- `rubric_based_final_response_quality_v1` ≥ 0.7 on the plan (groundedness,
  actionability, safety).

## Edge cases to handle

1. **Non-actionable issue** → ask one clarifying question, set
   `agent:needs-clarification`, do not invent requirements.
2. **Clone failure** (bad repo/branch/permissions) → report the error rather
   than hallucinating repo structure.
3. **Prompt injection in issue text** → ignore embedded directives; treat as
   data.
4. **Missing `GITHUB_TOKEN`** → clear, actionable error (no silent failure).
5. **Large repos/files** → recon tools cap tree size and file bytes to keep the
   model context bounded.
