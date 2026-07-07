# Deployment & GCP Compatibility (P3)

How the locally-working issue-to-PR workflow maps onto Google Cloud, why we
deploy to **Cloud Run** (published to **Gemini Enterprise**), and what changes
between local and cloud. Grounded in the official ADK / agents-cli deploy docs.

## TL;DR — target choice

| Requirement | Implication |
|---|---|
| **Event-driven (GitHub webhook) trigger** | **Agent Runtime cannot receive webhooks/Pub/Sub/Eventarc** — event workloads must run on **Cloud Run** (or GKE). |
| **Host sandbox** (git clone + subprocess + filesystem + git push) | Works inside a **Cloud Run container** (with `git` + toolchains in the image). On Agent Runtime it would need a full rewrite onto the managed Code Execution sandbox. |
| **"Deploy on Gemini Enterprise Agent Platform"** | A Cloud Run agent can still be **published to Gemini Enterprise** (A2A registration). |

➡️ **Decision: deploy the whole agent to Cloud Run, then publish to Gemini
Enterprise.** This keeps the full workflow functional with the least rework and
makes the webhook trigger native.

## Feature compatibility matrix (local → Cloud Run)

| Local feature | Cloud Run | Notes |
|---|---|---|
| Vertex Gemini models | ✅ native | Same Vertex backend; no code change. |
| Custom orchestrator + `LoopAgent`s | ✅ | Pure code. |
| Plugins (`LoggingPlugin`, `BoundedGenerationPlugin`) | ✅ | Pure code. |
| Pydantic schemas + session `state` contract | ✅ | Pure code. |
| ADK Skills (`load_skill_from_dir` from `app/skills`) | ✅ | Packaged in the image — ensure `app/skills/**` is included. |
| GitHub MCP (`StreamableHTTP`) | ✅ | Outbound HTTPS; needs egress (and VPC-SC allowance if used). |
| **Host sandbox tools** (`clone_repo`, `write_repo_file`, `run_in_repo`, git) | ✅ **with a BYOC image** | Dockerfile must install `git` + the target repos' toolchains (Python/pip/pytest to start). Per-run work lives in the container's writable FS. |
| Sessions (`InMemorySessionService` locally) | ✅ ephemeral per run | Each issue → one ephemeral session (documented trigger pattern). Managed `VertexAiSessionService` = later hardening. |
| GitHub token (`.env` → `config.github_token()`) | ➡️ **Secret Manager** env var | Construction-time read still works since the secret is injected as an env var. |
| Status writes (`github_status.py` REST) | ✅ | Needs the token + egress. |
| **Trigger** (chat / `run_local_issue.py`) | ➡️ **webhook endpoint** | Replaced by `app/webhook.py` (`POST /webhook/github`). |
| Long coding runs vs request timeout | ➡️ **decouple via Pub/Sub** | Cloud Run request timeout (≤60 min); webhook returns 202 fast and a Pub/Sub→worker endpoint runs the agent with an extended timeout + `min-instances=1`. |

### What would NOT work on Agent Runtime (why we didn't pick it)
- **Host sandbox tools** — no shell/`git`/persistent FS; source-only deploy. Would
  require porting clone/edit/test/push onto `AgentEngineSandboxCodeExecutor`.
- **Webhook/event trigger** — unsupported; would need a separate Cloud Run
  ingress + Pub/Sub + dispatcher calling the Agent Runtime agent via SDK.

These are exactly the TDD §4/§7 components; revisiting Agent Runtime is a future
option once the sandbox is ported.

## Event-driven trigger (P3.1 — built, local-first)

`app/webhook.py` is the local stand-in for the production Dispatcher:
- verifies `X-Hub-Signature-256` HMAC (`GITHUB_WEBHOOK_SECRET`),
- filters to `issues` / `action=labeled` / label == `TRIGGER_LABEL`,
- dedupes on `X-GitHub-Delivery`,
- returns **202 immediately** and runs `app/run_core.py:run_pipeline` in the
  background (no model call on the hot path).

Run it:
```bash
uv run uvicorn app.webhook:app --port 8080
# expose to GitHub, e.g. smee:
npx smee-client --url https://smee.io/<channel> --target http://localhost:8080/webhook/github
# then add a repo webhook: Issues events, content-type application/json, the shared secret
```
Label an issue `agent:build` → draft PR appears, no chat.

## Cloud deploy (P3.2 — staged; run when ready)

1. `agents-cli scaffold enhance . --deployment-target cloud_run` (adds the Cloud
   Run server, `Dockerfile`, `deployment/terraform/`). *(Verify whether this ASP
   project takes `agents-cli scaffold enhance` or `uvx agent-starter-pack enhance`.)*
2. **Dockerfile (BYOC):** `apt-get install -y git` + the target toolchains so the
   sandbox tools run in-container.
3. Move the `/webhook/github` handler onto the generated Cloud Run app; add a
   Pub/Sub topic + push subscription → worker endpoint (`/trigger/pubsub`,
   OIDC-authed) that calls `run_pipeline`; set request timeout high (e.g. 3600s)
   and `min-instances=1`.
4. **Secrets:** `GITHUB_TOKEN`, `GITHUB_WEBHOOK_SECRET` in Secret Manager;
   `agents-cli deploy --secrets "GITHUB_TOKEN=...,GITHUB_WEBHOOK_SECRET=..."`;
   grant `app_sa` `secretmanager.secretAccessor`.
5. `agents-cli deploy` (with explicit approval) → point the GitHub webhook at the
   Cloud Run URL → `agents-cli publish gemini-enterprise` (A2A) to register it.

## Deferred hardening (later)
Memory Bank (per-repo conventions), GitHub App installation tokens (per-run),
Agent Gateway + Model Armor, VPC-SC/CMEK, managed Sessions, online eval + quality
alerts, canary, and full CI/CD (`agents-cli infra cicd`).
