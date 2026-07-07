# Technical Design Document
## Event-Triggered, Asynchronous Multi-Agent GitHub Coding Workflow
### Built on Google ADK 2.0 · Deployed on Gemini Enterprise Agent Platform (Agent Runtime)

**Status:** Draft for review
**Owner:** Solutions Architecture
**Last updated:** 2026-06-29
**Audience:** Platform engineering, applied AI, DevEx, security/governance

> This document is grounded in the current official docs (ADK 2.0 GA, the ADK GitHub MCP integration, and Gemini Enterprise Agent Platform / Agent Runtime). Where a product caveat affects a design decision, it is called out inline. See **§13 References**.

---

## 1. Summary

We are building an **autonomous "issue-to-PR" engineering workflow**. When a GitHub issue is labeled with a trigger label (e.g. `agent:build`), an asynchronous multi-agent system wakes up, understands the requirement, clones the repo into a sandbox, plans and writes the code, writes and runs tests in an iterative loop, performs review, and opens a pull request linked back to the issue — with a human-in-the-loop (HITL) approval gate before any merge.

The system is implemented with **Google Agent Development Kit (ADK 2.0)** using a coordinator/sub-agent topology, integrates with GitHub via the **remote GitHub MCP server**, packages reusable procedural knowledge as **ADK Skills**, and is deployed and operated on the **Gemini Enterprise Agent Platform — Agent Runtime** (resource type `ReasoningEngine`), using **Sessions**, **Memory Bank**, **Code Execution / Sandbox**, **Observability**, and **Governance (Agent Gateway, Model Armor, IAM Agent Identity)**.

**Why this is non-trivial:** GitHub webhooks expect a fast (~seconds) `2xx` response, but a coding run takes minutes. Agent Runtime is request-driven. The architecture therefore **decouples ingestion from execution** with a queue, and pushes the heavy git/build/test work into a **managed sandbox**, not the agent's request thread.

---

## 2. Goals and non-goals

### 2.1 Goals
- G1 — Trigger an agent workflow **asynchronously** from a GitHub event (label add / issue open), with idempotent, signature-verified ingestion.
- G2 — Decompose the work across **specialized sub-agents** managed by an **orchestrator**, using ADK-native multi-agent primitives.
- G3 — Read **and write** GitHub (issues, labels, repos, branches, PRs, comments, checks) through **MCP tools** with least-privilege scoping.
- G4 — Run real code: clone, edit, build, and **execute tests in an isolated sandbox**, iterating until green or budget-exhausted.
- G5 — Always end in a **reviewable artifact** (a PR + summary comment), never an auto-merge without human approval.
- G6 — Be **observable, evaluable, governable, and cost-bounded** in production.

### 2.2 Non-goals (v1)
- Auto-merging to protected branches without human review.
- Operating on repositories the installed credential is not explicitly authorized for.
- Multi-repo / monorepo cross-cutting refactors in a single run (single repo, single issue scope in v1).
- Replacing CI; the agent's in-sandbox tests are a *fast inner loop*, not a substitute for the repo's CI gates on the PR.

---

## 3. Refined requirements

### 3.1 Functional requirements
| ID | Requirement |
|----|-------------|
| FR-1 | A configurable trigger (label value, or `issues.opened` with a label) starts exactly one workflow run per qualifying event. |
| FR-2 | The system posts a "🤖 picked up" acknowledgment comment + a status label (`agent:in-progress`) within seconds of accepting the event. |
| FR-3 | The Intake agent extracts a **structured spec** (problem, acceptance criteria, affected area, constraints) and may request clarification by commenting on the issue (bounded, optional). |
| FR-4 | The system clones the target repo/branch into a sandbox and produces a **repo map** (languages, build system, test framework, conventions). |
| FR-5 | The Planner produces an explicit **change plan** (files, approach, test strategy) recorded in session state and posted to the issue thread. |
| FR-6 | The Coder implements changes in the sandbox; the Tester writes/updates and **runs** tests; the two iterate in a bounded loop until tests pass or `max_iterations` is hit. |
| FR-7 | A Reviewer validates against acceptance criteria and runs security/lint checks before delivery. |
| FR-8 | On success, the Delivery agent creates a branch, commits, opens a **PR linked to the issue**, posts a summary, and sets `agent:needs-review`. On failure, it posts a diagnostic comment and sets `agent:failed`. |
| FR-9 | All runs are **resumable/cancelable**, and every run is traceable end-to-end. |
| FR-10 | A human can approve/deny risky steps (e.g., dependency changes, file deletions, opening the PR) via an HITL gate. |

### 3.2 Non-functional requirements
| ID | Requirement | Target (tune in §11) |
|----|-------------|----------------------|
| NFR-1 Latency (ack) | Webhook → 2xx | < 3 s (decoupled; no model call on the hot path) |
| NFR-2 Throughput | Concurrent runs | N configurable; queue-buffered, runtime autoscaled |
| NFR-3 Cost ceiling | Per-run token + sandbox budget | Hard cap; abort + report when exceeded |
| NFR-4 Reliability | Exactly-once *effect* on GitHub | Idempotency keys + dedupe on `delivery_id` |
| NFR-5 Security | Least privilege, no secret leakage to model context | Per-toolset MCP scoping, Model Armor, VPC-SC/CMEK |
| NFR-6 Auditability | Who/what/why for every GitHub write | Cloud Logging + Trace + Agent Gateway logs |
| NFR-7 Quality | Regression guard on agent behavior | Offline + online eval, quality alerts |

### 3.3 Assumptions (validate before build)
- A1 — GitHub access is via a **GitHub App installation** (preferred) or fine-grained PAT, scoped to specific repos. (Docs show PAT + remote MCP; an App is the enterprise-grade path for rotation/audit.)
- A2 — Target repos use mainstream toolchains the sandbox base image can support (Node/Python/Go/Java to start). BYOC container provides git + these toolchains.
- A3 — Trigger semantics: label-add on an issue. (Confirm whether PR-review events / comments should also trigger.)
- A4 — One issue → one PR. Human merges.
- A5 — Org is on Gemini Enterprise Agent Platform with Agent Runtime, Sessions, Memory Bank, and Code Execution available in the chosen region.

### 3.4 Open questions / decisions needed
- Q1 — **Branch protection & who merges?** Required reviewers, required CI checks, CODEOWNERS interplay.
- Q2 — **Clarification loop policy:** may the agent ask the issue author questions and wait, or must it proceed best-effort? (Affects use of `task` vs `single_turn` modes — see §5.3.)
- Q3 — **Secret handling for private deps** (private registries, submodules) inside the sandbox.
- Q4 — **Budget policy** per run and per repo; what to do on exhaustion (partial PR vs. abort).
- Q5 — **Model mix** per role (e.g. Gemini Flash for routing/intake, a stronger model for coding/review). ADK supports per-agent models and model routing.
- Q6 — Should review security findings use the GitHub MCP `code_security`/`dependabot` toolsets, the platform's own scanning, or both?

---

## 4. High-level architecture

```
                          (1) issue labeled "agent:build"
GitHub  ───────────────────────────────────────────────►  Webhook
  ▲  ▲                                                         │
  │  │                                                         ▼
  │  │                                            (2) Ingress / Edge (Cloud Run)
  │  │                                                - verify HMAC signature
  │  │                                                - filter event + label
  │  │                                                - dedupe on X-GitHub-Delivery
  │  │                                                - return 202 fast
  │  │                                                         │ publish
  │  │                                                         ▼
  │  │                                          (3) Pub/Sub topic  (durable queue)
  │  │                                                         │
  │  │                                                         ▼
  │  │                                   (4) Dispatcher (Cloud Run worker / Cloud Tasks)
  │  │                                        - acquire run lock (idempotency)
  │  │                                        - start async run on Agent Runtime
  │  │                                                         │ invoke
  │  │                                                         ▼
  │  │        ┌───────────────────── Gemini Enterprise Agent Platform ─────────────────────┐
  │  │        │  Agent Runtime (ReasoningEngine)                                            │
  │  │        │                                                                             │
  │  │        │   ┌──────────────  Orchestrator (root coordinator) ───────────────┐        │
  │  │        │   │  Intake → RepoContext → Planner → [Coder ⇄ Tester loop] →      │        │
  │  │        │   │  Reviewer → (HITL gate) → Delivery                             │        │
  │  │        │   └───────────────────────────────────────────────────────────────┘        │
  │  │        │        │ Sessions (run state)   │ Memory Bank (cross-run learnings)         │
  │  │        │        │ Code Execution/Sandbox (clone, build, run tests; BYOC)             │
  │  │        │        │ Observability (Trace/OTel, Logging, Monitoring) · Eval             │
  │  │        │        │ Governance: Agent Gateway · Model Armor · IAM Agent Identity       │
  │  │        └──────────────────────────────┬──────────────────────────────────────────────┘
  │  │                                       │ GitHub MCP (read-scoped + write-scoped)
  │  └───────────────────────────────────────┘  branch/commit/PR/comment/labels/checks
  └──────────────────────────────────────────  status comments + PR back to the issue
```

**Key decoupling principle:** steps (2)–(4) exist precisely because GitHub webhooks time out fast while agent runs take minutes. No LLM call happens on the webhook's synchronous path.

### 4.1 Component responsibilities
- **Ingress/Edge (Cloud Run):** signature verification (`X-Hub-Signature-256`), event/label filtering, dedupe on `X-GitHub-Delivery`, immediate `202`. Stateless, tiny, fast. Optionally fronted by **Apigee/Agent Gateway** for policy.
- **Queue (Pub/Sub):** durability, retry, backpressure, replay. Decouples burst of GitHub events from bounded runtime concurrency.
- **Dispatcher:** idempotency lock per `(repo, issue, delivery_id)`, maps event → run config, invokes the deployed agent **asynchronously** and tracks the run handle. (Pattern aligns with ADK *Ambient Agents* / event-driven invocation; the dispatcher is what turns a webhook into an agent run.)
- **Agent Runtime (`ReasoningEngine`):** hosts the orchestrator + sub-agents; provides managed scaling, Sessions, Memory Bank, Sandbox, Observability, and Governance integration. ADK has **full integration** here (vs. SDK-template support for other frameworks).
- **GitHub MCP server (remote):** the agents' hands into GitHub.

---

## 5. Agent roster, personas, and orchestration

### 5.1 Design rationale for the topology
The pipeline is **mostly deterministic in sequence** (intake → context → plan → implement/test → review → deliver) with **one genuinely iterative sub-loop** (coder ⇄ tester). ADK gives us three relevant tools:

1. **Workflow agents** — `SequentialAgent`, `LoopAgent`, `ParallelAgent` — deterministic control of *which agent runs when*.
2. **LLM-driven coordinator + sub-agents with modes** — a coordinator delegates to sub-agents that auto-return; sub-agents carry a **mode** (`chat`, `task`, `single_turn`).
3. **Graph workflows** (ADK 2.0) — explicit routes, data handling, human-input nodes, dynamic routing.

**Recommended composition for v1:** a top-level **`SequentialAgent` "pipeline"** for the backbone, with the **coder/tester** wrapped in a **`LoopAgent`**, and the **orchestrator** implemented as the pipeline owner that also handles GitHub status side-effects via callbacks. This gives predictable, debuggable control flow and clean traces. Use LLM-driven delegation only where genuine branching judgment is needed (e.g., "is this issue actionable / which area of the codebase").

> **Important ADK 2.0 caveat:** `task` mode is **disabled inside graph-based workflows** in ADK Python v2.0.0 (expected to return later), and `task`-mode agents must be **leaf agents**. So for parallelizable, no-user-interaction steps inside the deterministic pipeline, prefer **`single_turn`** (which supports **parallel execution in isolated session branches**). Reserve `task`/`chat` modes for the *clarification* path under an LLM coordinator, outside the graph. Design around this explicitly rather than discovering it at runtime.

### 5.2 The sub-agents (personas)

| # | Agent (persona) | Mission | Mode | Primary tools / skills | Key inputs → outputs |
|---|-----------------|---------|------|------------------------|----------------------|
| 0 | **Orchestrator / "Conductor"** | Owns run lifecycle, sequencing, state, HITL gates, GitHub status updates, budget enforcement. Does *not* write code. | root coordinator (no `mode`) | callbacks, GitHub MCP (write: labels/comments), state | event payload → terminal status + PR link |
| 1 | **Intake / "Requirement Analyst"** | Read issue + linked context; classify; extract acceptance criteria; decide actionability; optional clarification. | `task` (if clarifying) else `single_turn` | GitHub MCP (read: issues, discussions), `skill: spec-extraction` | issue → `IssueSpec` |
| 2 | **Repo Context / "Codebase Navigator"** | Clone repo to sandbox; build repo map; locate relevant files; detect build/test tooling & conventions. | `single_turn` | Sandbox/Code Execution, GitHub MCP (read: repos), `skill: repo-recon` | spec → `RepoContext` |
| 3 | **Planner / "Architect"** | Convert spec + context into a concrete change plan + test strategy. | `single_turn` | (reasoning), Example Store | spec+context → `ChangePlan` |
| 4 | **Coder / "Implementer"** | Apply the plan: edit files in sandbox per conventions. | leaf of loop | Sandbox, `skill: coding-standards`, `skill: safe-edits` | plan(+failing tests) → diff |
| 5 | **Tester / "Verifier"** | Write/extend tests; **run** the suite in sandbox; report structured results. | leaf of loop | Sandbox/Code Execution, `skill: test-authoring` | diff → `TestReport` |
| 6 | **Reviewer / "Critic"** | Check against acceptance criteria; lint; security (Dependabot/code-scanning); risk-flag. | `single_turn` | GitHub MCP (read: `code_security`,`dependabot`), Sandbox | diff+report → `ReviewVerdict` |
| 7 | **Delivery / "PR Author"** | Branch, commit, push, open PR linked to issue, summary comment, set labels. | `task`/leaf | **write-scoped** GitHub MCP (`repos`,`pull_requests`,`issues`,`labels`) | approved diff → PR URL |

**Coder ⇄ Tester loop:** a `LoopAgent` containing Coder then Tester, with an **exit condition** = `TestReport.passed == true`, and a hard `max_iterations` (e.g. 4) tied to the budget. On loop exhaustion, control returns to the orchestrator, which routes to a "diagnostic" delivery (failure comment) instead of PR.

### 5.3 Mode selection cheat-sheet (so behavior is predictable)
- **`single_turn`** → no user interaction, auto-return, **parallelizable**, isolated branch context. Use for Intake (non-clarifying), RepoContext, Planner, Reviewer. (RepoContext + an early Reviewer "static scan" could even run in parallel.)
- **`task`** → may ask the user clarifying questions, auto-returns on `complete_task`, **must be a leaf, not allowed inside graph workflows in 2.0.0.** Use only for the optional Intake clarification path and Delivery (if you want it to confirm before opening a PR), driven by an LLM coordinator rather than a graph node.
- **`chat`** → full interaction, manual handoff. Not used in the autonomous path; reserved for an interactive "/agent" console variant.

### 5.4 Data contracts (enforced with `input_schema` / `output_schema`)
ADK supports structured I/O on agents/nodes. Define Pydantic schemas and attach them so hand-offs are validated:

- `IssueSpec { issue_id, repo, title, problem, acceptance_criteria[], constraints[], affected_area, is_actionable, clarification_needed }`
- `RepoContext { default_branch, languages[], build_system, test_command, relevant_paths[], conventions_notes }`
- `ChangePlan { steps[], files_to_change[], test_strategy, risk_flags[] }`
- `TestReport { passed, total, failed[], logs_ref, coverage? }`
- `ReviewVerdict { approved, criteria_results[], security_findings[], required_changes[] }`
- `DeliveryResult { branch, pr_url, status_label }`

State keys mirror these (`state["issue_spec"]`, etc.) so every agent reads/writes a known slot; this is also what makes traces and evals legible.

---

## 6. GitHub integration (MCP) — least privilege by design

Use the **remote GitHub MCP server** via ADK's `McpToolset` with `StreamableHTTPConnectionParams`. Critically, **split into two toolset instances** so most agents are read-only and only Delivery can write:

```python
from google.adk.agents import Agent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

GH_MCP_URL = "https://api.githubcopilot.com/mcp/"

def github_read(token: str) -> McpToolset:
    return McpToolset(connection_params=StreamableHTTPConnectionParams(
        url=GH_MCP_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "X-MCP-Toolsets": "repos,issues,labels,discussions,code_security,dependabot",
            "X-MCP-Readonly": "true",      # read-only for analysts/reviewers
        },
    ))

def github_write(token: str) -> McpToolset:
    return McpToolset(connection_params=StreamableHTTPConnectionParams(
        url=GH_MCP_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "X-MCP-Toolsets": "repos,pull_requests,issues,labels",
            "X-MCP-Readonly": "false",     # ONLY the Delivery + status path
        },
    ))
```

**Toolset mapping to personas** (GitHub MCP toolsets include `repos`, `issues`, `labels`, `pull_requests`, `discussions`, `actions`, `code_security`, `dependabot`, `projects`, `notifications`, etc.):
- Intake → `issues`, `discussions` (read)
- RepoContext / Coder / Tester → repo read for browsing; **actual clone happens in the sandbox via git**, not MCP, for performance and to keep large file content out of model context.
- Reviewer → `code_security`, `dependabot` (read)
- Orchestrator → `issues`, `labels` (write, narrow) for status comments/labels
- Delivery → `repos`, `pull_requests`, `issues`, `labels` (write)

**Hardening:**
- Prefer a **GitHub App installation token** (short-lived, per-repo, auditable) over a long-lived PAT; the dispatcher mints/refreshes it per run and passes it into the run context (never logged, never placed in prompts).
- Set `X-MCP-Readonly: true` everywhere except the single write path. A bad toolset string makes the server fail closed (400) — fail-safe.
- Route MCP egress through **Agent Gateway** so tool calls are policy-checked and logged centrally.

---

## 7. Sandbox / Code Execution (where real work happens)

The clone/build/test cycle runs in **Agent Runtime Code Execution / Sandbox** — a secure, isolated, managed environment — **not** in the agent's reasoning thread.

- **BYOC (custom container):** base image with `git`, language toolchains (Node, Python, Go, Java), package managers, and a non-root user. Use **sandbox templates** + **snapshots** so warm environments start fast and the post-clone state can be checkpointed between loop iterations.
- **Lifecycle per run:** create sandbox → shallow-clone target branch → install deps → Coder writes files → Tester runs `test_command` → capture structured results + logs (as **Artifacts**) → on exit, push branch via the write-scoped path → tear down.
- **Isolation & security:** no inbound network except allow-listed registries; secrets injected via the platform, never echoed into model context; VPC-SC / PSC-I for private dependency hosts.
- **Budgeting:** wall-clock + CPU + token caps enforced by the orchestrator; loop `max_iterations` bounds test cycles.

> Heavy, deterministic steps (dependency install, full test runs) belong in the sandbox/job, with the agent *orchestrating and interpreting results* rather than streaming build output through the model. This is the single biggest cost/latency lever.

---

## 8. State, sessions, and memory

- **Sessions (Agent Platform Sessions):** the per-run source of truth for conversation/events and `state`. One session per workflow run, keyed by `(repo, issue, delivery_id)`. Enables **resume** and **cancel**, and clean traces.
- **State:** the structured slots from §5.4. Treat state as the contract between agents; avoid free-text hand-offs.
- **Memory Bank:** cross-run, longer-term learnings — e.g. per-repo conventions, "this repo's test command is X", recurring review rules, known flaky tests. Scope memories per repo/org; control access with IAM conditions. This is what makes the system *improve* on a repo over time instead of re-discovering context every run.
- **Example Store (optional):** few-shot exemplars of good plans/PRs to steer Planner/Coder quality.

---

## 9. Human-in-the-loop (HITL)

Two gates, both implemented with ADK graph **human-input** nodes / `task`-mode confirmation, surfaced *in GitHub* (so reviewers stay in their tool):

1. **Pre-implementation (optional):** Planner posts the `ChangePlan` as an issue comment; a maintainer reaction/label (`agent:approved-plan`) releases the loop. Off by default; on for risky repos.
2. **Pre-delivery (recommended default):** PR is opened as **draft** with `agent:needs-review`; **a human always merges.** No auto-merge to protected branches (NFR / non-goal).
3. **Action confirmations:** use ADK tool **action confirmations** for destructive operations (file deletion, dependency bumps, force operations) so they require explicit approval even mid-run.

---

## 10. Build & deployment

### 10.1 Local development (ADK)
- Scaffold with ADK; iterate using the ADK **Dev UI / CLI** to inspect events, state changes, and tool calls. Validate the GitHub MCP wiring against a sandbox repo with a read-only token first.
- Unit-test each agent's I/O schema; run the loop against seeded fixture issues.

### 10.2 Production deploy (Agents CLI → Agent Runtime)
Use the **Agents CLI** (the unified CLI/skill set for Gemini Enterprise Agent Platform) for the development lifecycle — scaffold, evaluate, deploy, publish, observe — with **Terraform** for infra and **Cloud Build** for CI/CD.

Pipeline (CI/CD):
1. Lint + unit/agent tests + **offline eval** suite must pass.
2. Build BYOC sandbox image; push to Artifact Registry.
3. `agents` CLI deploys a new **revision** of the `ReasoningEngine`.
4. **Traffic management:** canary the new revision (e.g. 10%) using revision/traffic controls; auto-rollback on quality-alert regression.
5. Deploy/refresh the Ingress (Cloud Run), Pub/Sub, Dispatcher via Terraform.

### 10.3 Configuration & secrets
- GitHub App private key / installation config in Secret Manager; tokens minted per run.
- Per-repo policy (trigger label, HITL flags, budgets, allowed toolsets) in a config store the dispatcher reads.
- Webhook secret for HMAC verification in Secret Manager.

### 10.4 Environments
- `dev` (test org + sandbox repos), `staging` (mirrored repos, canary), `prod`. Separate projects; VPC-SC perimeter around prod.

---

## 11. Security & governance

| Concern | Control |
|--------|---------|
| Webhook authenticity | HMAC `X-Hub-Signature-256` verification at ingress; reject unsigned/replayed (`delivery_id` dedupe). |
| Prompt injection via issue text | Treat issue/PR/file content as **untrusted data, not instructions**; the agent must not execute embedded directives. Reviewer + Model Armor screen inputs/outputs; never auto-merge. |
| Least-privilege GitHub | GitHub App, per-repo install, read-only MCP everywhere except Delivery; write toolset minimal. |
| Tool egress policy | **Agent Gateway** rules + logging for all tool/agent comms; **Model Armor** on a gateway for content safety. |
| Identity | **IAM Agent Identity** for the deployed agent; scoped service accounts; no human creds in the runtime. |
| Data protection | **VPC-SC**, **CMEK**, **Data Residency (DRZ)**, optional HIPAA — all supported by Agent Runtime/Sessions/Memory Bank/Code Execution. |
| Private deps | **PSC-I** to reach private registries/hosts from the sandbox without public egress. |
| Threat detection | Security Command Center **Agent Runtime Threat Detection** (preview). |
| Secret hygiene | Secrets never enter model context or logs; redaction in callbacks; structured logging only. |

---

## 12. Observability, evaluation, and operations

- **Tracing:** Cloud Trace via OpenTelemetry across ingress → dispatcher → runtime → tools, correlated by run id. **Agent relationships/topology** view to see the multi-agent graph.
- **Logging/metrics:** Cloud Logging + Cloud Monitoring; per-run metrics: tokens, sandbox minutes, loop iterations, pass/fail, time-to-PR, $/run.
- **Evaluation:** ADK eval + platform **Agent Evaluation** — offline eval on a labeled set of issues (does the plan match? do tests pass? does the PR satisfy criteria?), **online monitors** + **quality alerts** in prod, with **failure-cluster analysis** and **prompt optimization** to improve weak roles.
- **SLOs & alerts:** ack latency, run success rate, escape rate (PRs reviewers reject), cost per successful PR. Page on queue backlog and quality-alert regressions.
- **Runbooks** (map to the official troubleshooting guides): runtime/env setup, agent creation, deployment, managing deployed agents, code execution, Memory Bank, Agent Gateway connectivity. (Links in §13.)

---

## 13. References (official docs consulted)

**ADK**
- ADK overview / components: https://adk.dev/get-started/about/
- ADK 2.0 (graph workflows + collaborative agents): https://adk.dev/2.0/
- Collaborative workflows (modes: chat/task/single_turn): https://adk.dev/workflows/collaboration/
- Multi-agent workflows & patterns: https://adk.dev/workflows/ · https://adk.dev/workflows/patterns/
- Graph workflows (routes, data handling, human input, dynamic): https://adk.dev/graphs/
- Workflow (template) agents — Sequential/Loop/Parallel: https://adk.dev/agents/workflow-agents/
- GitHub MCP integration: https://adk.dev/integrations/github/
- MCP tools: https://adk.dev/tools-custom/mcp-tools/  · Skills: https://adk.dev/skills/
- Sessions & Memory: https://adk.dev/sessions/  · Ambient/Resume/Cancel: https://adk.dev/runtime/
- Tutorials: multi-tool agent, agent team, coding with AI (under https://adk.dev/tutorials/)

**Gemini Enterprise Agent Platform (Agent Runtime)**
- Agent Runtime overview: https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/runtime
- Deploy an agent: https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/runtime/deploy-an-agent
- Sessions: https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/sessions
- Memory Bank: https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/memory-bank
- Sandbox / Code Execution: https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/sandbox
- Optimize / observability / eval: https://docs.cloud.google.com/gemini-enterprise-agent-platform/optimize
- Agents CLI: https://google.github.io/agents-cli/ · Skills: https://google.github.io/agents-cli/reference/skills/
- Troubleshooting (runtime-setup, agent-creation, agent-deployment, managing-deployed-agents, code-execution, memory-bank): under https://docs.cloud.google.com/gemini-enterprise-agent-platform/troubleshooting/

> **Verify-before-build flags:** (a) `task`-mode-in-graph is disabled in ADK Python 2.0.0 — confirm current status when you start; (b) Agent Runtime resource is `ReasoningEngine` and the SDK is moving to a client-based design — pin SDK versions; (c) several platform features (Agent Identity, Agent Gateway, Threat Detection, Example Store) are **Preview** — confirm GA/region availability for prod.

---

## 14. Phased delivery plan

| Phase | Scope | Exit criteria |
|-------|-------|---------------|
| P0 — Spike | Ingress→queue→dispatcher→a single ADK agent that comments on the labeled issue. Read-only GitHub MCP. | Webhook reliably triggers an agent comment in < 3 s ack. |
| P1 — Read & plan | Intake + RepoContext + Planner; sandbox clone + repo map; plan posted to issue. No writes beyond comments. | Plans are coherent on a fixture repo; full trace visible. |
| P2 — Build loop | Coder ⇄ Tester `LoopAgent` in sandbox; Reviewer. Draft PR via write-scoped MCP; human merges. | Green tests + draft PR on ≥X seeded issues; budget caps enforced. |
| P3 — Harden | Memory Bank per-repo, HITL gates, Agent Gateway + Model Armor, VPC-SC/CMEK, eval suite + quality alerts, canary deploys. | Security review passed; online eval + rollback wired. |
| P4 — Scale & GTM | Multi-repo onboarding config, dashboards, runbooks, demo. | Repeatable onboarding; cost/SLO dashboards live. |

---

## 15. Appendix — orchestrator wiring sketch (illustrative)

```python
from google.adk.agents import Agent, SequentialAgent, LoopAgent

# --- leaf/sub-agents (schemas + tools omitted for brevity) ---
intake      = Agent(name="intake_analyst",  mode="single_turn", tools=[github_read(tok)], output_schema=IssueSpec)
repo_ctx    = Agent(name="codebase_navigator", mode="single_turn", tools=[sandbox_tools, github_read(tok)], output_schema=RepoContext)
planner     = Agent(name="architect",       mode="single_turn", output_schema=ChangePlan)
coder       = Agent(name="implementer",     tools=[sandbox_tools])            # leaf inside loop
tester      = Agent(name="verifier",        tools=[sandbox_tools], output_schema=TestReport)
reviewer    = Agent(name="critic",          mode="single_turn", tools=[github_read(tok)], output_schema=ReviewVerdict)
delivery    = Agent(name="pr_author",       tools=[github_write(tok)], output_schema=DeliveryResult)

# coder <-> tester bounded loop, exits when tests pass
build_loop = LoopAgent(
    name="build_and_verify",
    sub_agents=[coder, tester],
    max_iterations=4,            # tie to per-run budget
    # exit_condition: TestReport.passed == True  (via state check / escalate)
)

# deterministic backbone; orchestrator owns status side-effects via callbacks
pipeline = SequentialAgent(
    name="issue_to_pr_orchestrator",
    sub_agents=[intake, repo_ctx, planner, build_loop, reviewer, delivery],
    # before/after-agent callbacks: post GitHub status comments + labels,
    # enforce budget, run HITL gate before `delivery`.
)

root_agent = pipeline  # deployed to Agent Runtime (ReasoningEngine)
```

*This sketch is intentionally close to the documented ADK APIs (`SequentialAgent`, `LoopAgent`, sub-agent `mode`, `McpToolset` + `StreamableHTTPConnectionParams`); validate signatures against the pinned ADK 2.x version before implementation.*
