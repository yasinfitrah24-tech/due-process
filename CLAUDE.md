# Due Process

Abuse desk tool for cloud/hosting providers. An abuse report (URL + claim) is treated as an untrusted claim. The URL is reproduced in a disposable, locked-down browser container; only evidence reproduced there can move an action. Output: graded verdict (Proven / Suspicious / Not proven), statement of reasons, customer notice, reporter reply. An analyst approves the final action.

Principle: "A report is a claim. Only evidence reproduced in a disposable browser can move an action."

## Hard rules (never break these)
1. Report text is untrusted data. It is passed to the model only as quoted data, never as instructions, and never decides targets, visit steps, or actions.
2. The action target (customer) is derived from the reported URL's host via the hosting inventory, never from report text.
3. The model has no tools that act. Verdict level is computed in code from deterministic signals. Model output is a claim that must cite signal IDs that exist; otherwise it is rejected and flagged.
4. Actions only through analyst approval. Allowed actions depend on verdict level: Not proven → Close; Suspicious → Warn/Close; Proven → Suspend/Warn/Close.
5. The browser never fills in or submits forms or credentials.
6. No secrets in the sandbox. No API keys in the repo; use .env (gitignored) and .env.example.
7. All LLM calls go through Vultr Serverless Inference (OpenAI-compatible, https://api.vultrinference.com/v1). No other LLM provider.
8. Test pages use fictional brands only, are clearly labeled TEST PAGE, and contain no real credential-collecting logic.

## Stack
- Python 3.12, FastAPI, SQLite, Jinja2 + HTMX (server-rendered, 3 screens: Queue, Case, Approval)
- Sandbox: one Docker container per case from `sandbox/` image (Playwright Python on mcr.microsoft.com/playwright/python; the image does NOT include the Python `playwright` package, so the Dockerfile installs a pinned version)
- Launcher: separate container, the only one holding the Docker socket; its whole API is POST /visit. The app has no socket and no Docker client
- Egress: separate proxy container; sandbox reaches the outside only through it; proxy logs all requests and blocks private ranges and 169.254.169.254
- Deploy: one Vultr VM (app + launcher + Docker sandbox + proxy + test site) via docker compose

## Sandbox contract (mode B3, proven in probe 1 Nov)
docker run flags per case:
--user pwuser --read-only --tmpfs /tmp:rw,size=256m --cap-drop ALL --security-opt no-new-privileges
--security-opt seccomp=sandbox/seccomp/playwright-<ver>-chroot.json
--memory 1g --memory-swap 1g --pids-limit 256 --shm-size 512m --cpus 1 --rm
no env from host; network = sandbox net (egress via proxy only); hard timeout 60 s.
Chromium launched with its own sandbox ON (chromium_sandbox=True). Seccomp profile = Playwright upstream profile with only `chroot` allowed unconditionally (see sandbox/seccomp/patch_chroot.py).
Sandbox proof recorded per case: namespaces of Chromium processes, container id, teardown confirmation (container gone).
Max parallel sandboxes: 4.

## Layout
app/       FastAPI app, templates, DB models, API (POST /api/reports, approvals)
agent/     Vultr Inference client and model roles: claim extraction, vision claim, verdict claim, document drafts (schemas validated)
sandbox/   Dockerfile, runner (visit), seccomp/, launch + teardown
launcher/  internal visit(url) service around sandbox/launch.py
egress/    egress proxy
testsite/  fictional-brand test pages (own container)
corpus/    labeled reports + page list for eval
eval/      baselines (text-only triage, fetch-without-browser) + run_eval.py
docs/      spec.md, architecture.md

## Commands
make up / make down / make test / make eval

## Prior work (disclose in README)
Non-AI sandbox scaffolding (runner Dockerfile, seccomp profile + chroot patch, run/test scripts) comes from a probe built before the hackathon (Nov 1). Organizers allow pre-existing non-AI scaffolding if disclosed. All AI functionality (claim extraction, vision claims, verdict claims, document drafts) and the web app are built Nov 3-8.

## Working rules
- Commit, do not push. Branch per person and task: y/<task> (Yasin), v/<task> (Vincent). main changes only via PR merged by Yasin.
- Each task ends with the LAPORAN format in the prompt.
- Don't change hard rules or sandbox flags without being asked.
