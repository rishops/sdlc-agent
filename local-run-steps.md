# Local Run — Event-Driven Workflow (P3.1)

Step-by-step to test the **GitHub webhook → issue-to-PR** workflow on your
machine. Labeling an issue `agent:build` drives the whole pipeline to a draft PR
— no chat. The webhook ingress runs locally; a tunnel forwards GitHub events to
it.

```
GitHub (label issue)  ──webhook──►  smee.io tunnel  ──►  localhost:8080/webhook/github
                                                          (verify HMAC → filter → dedupe
                                                           → 202 → background run → draft PR)
```

---

## 0. Prerequisites

- **uv** installed, and this repo cloned.
- **git** on your PATH (the sandbox clones/commits via the local `git`).
- **Node/npx** (for the `smee-client` tunnel) — or ngrok/cloudflared if you prefer.
- **Google Cloud / Vertex AI** access:
  ```bash
  gcloud auth application-default login
  gcloud services enable aiplatform.googleapis.com --project YOUR_PROJECT
  ```
- A **throwaway GitHub test repo** with at least one open **issue** to act on.
- A **fine-grained PAT** scoped to that repo with these permissions (P2b opens a
  real PR, so writes are required):
  - **Metadata:** Read
  - **Contents:** Read and Write
  - **Issues:** Read and Write
  - **Pull requests:** Read and Write

---

## 1. Install dependencies

```bash
cd /Users/rishabh/projects/sdlc-agent
make install        # or: uv sync
```

## 2. Configure `.env`

```bash
cp .env.example .env   # if you don't have one yet
```

Set at least:
```
GOOGLE_GENAI_USE_VERTEXAI=True
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=global

GITHUB_TOKEN=github_pat_xxx          # the fine-grained PAT above
TARGET_REPO=your-user/your-test-repo
TRIGGER_LABEL=agent:build
GITHUB_WEBHOOK_SECRET=pick-a-long-random-string

SANDBOX_BACKEND=host
POST_STATUS=true
```

> `GITHUB_TOKEN` is required even to import the agent. `GITHUB_WEBHOOK_SECRET` is
> what you'll also paste into the GitHub webhook config (Step 5) — they must match.

## 3. (Sanity check) Run one issue directly — no webhook

Confirms creds/sandbox/PR path before involving the tunnel:
```bash
uv run python scripts/run_local_issue.py --repo your-user/your-test-repo --issue 1
```
Expect the event stream → plan approved → build loop green → review approved →
a **draft PR** on the repo. If this works, the pipeline itself is healthy.

## 4. Create the trigger label in the repo

The agent fires on the **`agent:build`** label, so it must exist to be applied:
- GitHub → your repo → **Issues → Labels → New label** → name `agent:build`.
- (Optional) pre-create the status labels to avoid harmless 422s:
  `agent:in-progress`, `agent:plan-ready`, `agent:plan-needs-revision`,
  `agent:tests-green`, `agent:build-failed`, `agent:changes-requested`,
  `agent:needs-review`, `agent:needs-clarification`.

## 5. Start the webhook server

```bash
uv run uvicorn app.webhook:app --port 8080
```
Quick health check in another terminal:
```bash
curl -s localhost:8080/healthz      # → {"status":"ok"}
```

## 6. Start the tunnel (smee)

1. Open https://smee.io and click **Start a new channel** — copy the channel URL
   (e.g. `https://smee.io/AbCdEf123`).
2. In a new terminal:
   ```bash
   npx smee-client --url https://smee.io/SzCxZ15ctqcVvIMf \
     --target http://localhost:8080/webhook/github
   ```
   Leave it running — it forwards every delivery to your local server.

## 7. Add the webhook in GitHub

Repo → **Settings → Webhooks → Add webhook**:
- **Payload URL:** the smee channel URL from Step 6 (`https://smee.io/SzCxZ15ctqcVvIMf`)
- **Content type:** `application/json`
- **Secret:** the same value as `GITHUB_WEBHOOK_SECRET`
- **Which events:** *Let me select individual events* → check **Issues** only
- **Active:** ✓ → **Add webhook**

GitHub sends a `ping` first; the server ignores it (returns 200 `ignored`).

## 8. Trigger it

Open an issue (e.g. "Add a `/health` endpoint that returns 200") and **add the
`agent:build` label**.

Watch:
- **smee terminal** — forwards the `issues/labeled` delivery.
- **uvicorn terminal** — `Accepted trigger for <repo>#<n>` then the
  `LoggingPlugin` trace through intake → plan → build → review → delivery.
- **GitHub** — within seconds, a "🤖 picked up" comment + `agent:in-progress`;
  a few minutes later a **draft PR** (`Closes #n`) and `agent:needs-review`.

The webhook returns **202** instantly; the run finishes in the background.

## 9. Verify de-duplication

GitHub → repo → Settings → Webhooks → your hook → **Recent Deliveries** → pick the
labeled delivery → **Redeliver**. The server returns `{"status":"duplicate"}` and
does **not** start a second run.

---

## Optional — simulate a delivery without GitHub

Drive the local server directly with a correctly-signed payload (this starts a
real run against `--repo`/issue you put in the JSON):
```bash
uv run python - <<'PY'
import hashlib, hmac, json, os, urllib.request
secret = os.environ["GITHUB_WEBHOOK_SECRET"].encode()
payload = {
    "action": "labeled",
    "label": {"name": "agent:build"},
    "repository": {"full_name": "your-user/your-test-repo"},
    "issue": {"number": 1},
}
body = json.dumps(payload).encode()
sig = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
req = urllib.request.Request(
    "http://localhost:8080/webhook/github", data=body, method="POST",
    headers={"Content-Type": "application/json", "X-GitHub-Event": "issues",
             "X-GitHub-Delivery": "manual-1", "X-Hub-Signature-256": sig},
)
print(urllib.request.urlopen(req).status)   # → 202
PY
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `401 bad signature` | `GITHUB_WEBHOOK_SECRET` in `.env` ≠ the secret in the GitHub webhook config. |
| Webhook 200 `ignored` on label | Label name ≠ `TRIGGER_LABEL`, or event wasn't `issues/labeled` (only the Issues event is wired). |
| `RuntimeError: GITHUB_TOKEN is not set` at startup | `.env` missing/incomplete — required to import the agent. |
| Model **404** | `GOOGLE_CLOUD_LOCATION` should be `global`; confirm Gemini 3 access. Don't rename the model. |
| `git clone` / push fails | PAT missing **Contents: R/W** or not scoped to the repo. |
| No PR, `agent:changes-requested` | Reviewer rejected — read the issue comment for the required changes. |
| No PR, `agent:build-failed` | Tests never went green within `MAX_LOOP_ITERATIONS`. |
| Label add returns 422 (in logs) | Status label doesn't exist — pre-create it (Step 4) or ignore; it's best-effort. |
| Run floods identical text then stops | Model degeneration; bounded by `MAX_OUTPUT_TOKENS`. Re-label to retry; or set `MODEL_CODER=gemini-3-pro-preview`. |

> The webhook server hot-reloads only if started with `--reload`; for code changes
> just restart `uvicorn`.
