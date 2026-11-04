# Due Process

**Every abuse report is reproduced in a throwaway browser before anyone gets suspended.**

Live demo: <https://45-76-241-186.sslip.io>. No access code. Every customer and action there is simulated.

## The problem

Hosting providers get abuse reports all day: "there is a phishing site on your server". Acting on the report alone is
risky in both directions. Suspend on a false report and you punish an innocent customer. Open the URL yourself and a
hostile page gets a browser inside your network. A report can also be written to manipulate whatever reads it.

## The principle

**A report is a claim. Only evidence reproduced in a disposable browser can move an action.**

- Report text is untrusted data. It never picks a target, a visit step or an action.
- The target comes from the URL's host through the hosting inventory.
- The verdict (Proven, Suspicious, Not proven) is computed in code from deterministic signals, and stored before any
  model runs.
- Models have no tools. Their output is a claim that must cite signals that exist, or it is rejected and shown as
  rejected.
- An analyst approves every action. The verdict limits which actions are allowed.
- The browser never fills in or submits anything.

## How it works

```text
report ──► app ──► launcher ──► disposable sandbox ──► egress proxy ──► the page
            │     (only holder    (Chromium, one per     (logs every
            │      of the Docker   case, removed after)   request, blocks
            │      socket)                                private ranges and
            │                                             cloud metadata)
            ▼
   signals ► verdict ► model claims ► analyst ► simulated action and letters
                       (Vultr Serverless Inference)
```

| Part | What it does |
|---|---|
| `app` | FastAPI, SQLite, Jinja2 and HTMX. Signals, verdict, case screens, audit log |
| `launcher` | One route, `POST /visit`. Starts a sandbox with fixed flags and confirms it is gone |
| sandbox | Playwright Chromium in a read-only, non-root container with all capabilities dropped, seccomp, memory and process limits and a 60 s limit. Chromium's own sandbox stays on |
| `egress` | The only thing on the sandbox network. Public addresses on ports 80 and 443 only |
| Caddy | TLS. The only service with a public port |

Each case records the proof: the container id, the namespaces of Chromium's processes, the proxy log for that visit,
and confirmation that the container is gone. Evidence is read from outside the page's JavaScript context, through
browser network events, the Chrome DevTools Protocol and an isolated world, so page scripts cannot touch the instruments. Each item is
marked `observed`, `declared_in_script` or `model_claim`.

More detail: [docs/architecture.md](docs/architecture.md).

## Vultr services used

| Service | Use |
|---|---|
| Vultr Cloud Compute | One VM runs the whole stack, including every sandbox |
| Vultr Serverless Inference | All model calls: `deepseek-v4.1-flash-1017` for text, `nemotron-3-nano-omni-30b-a3b-reasoning` for screenshots |

The models extract what the reporter alleges, describe the screenshot, write the evidence statement, and draft the
statement of reasons, customer notice and reporter reply. None of them can change a verdict. Model spending stops at
USD 15 in total and USD 2 a day; after that, documents come from a code template and the verdicts keep working.

## Run it

Needs Docker with compose, and Python 3.12 for the tests.

```bash
cp .env.example .env
make up
```

Open <http://localhost:8000>. With no key in `.env`, the model roles use a mock. Set `VULTR_API_KEY`, `VULTR_MODEL` and
`VULTR_VISION_MODEL` to use Vultr Serverless Inference.

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
make test
make test-docker
make eval
```

`make reset-demo` deletes every case and rebuilds one example per test page. Deployment: [docs/deploy.md](docs/deploy.md).

## Evaluation

From `make eval`: 47 labeled cases on fictional test pages, one run each, model cost USD 0.17. Every case is named in
[docs/eval.md](docs/eval.md), next to the earlier 26-case version. For Due Process the action is the strongest its verdict
allows, which is the worst an analyst could do.

| Approach | Wrongful suspend | Abuse missed | Injection changed the outcome | Twin pairs right |
|---|---|---|---|---|
| Due Process | 1 of 25 | 2 of 22 | 0 of 4 | 8 of 8 |
| Text-only triage | 11 of 25 | 3 of 22 | 0 of 4 | 0 of 8 |
| Fetch without browser | 1 of 25 | 13 of 22 | not applicable: it never reads the report | 4 of 8 |

**Twin pairs** give one identical report to two pages, one abuse and one legitimate, on neutral hosts of one customer.
They test what cannot be known from the text, and they were added on purpose after the first version of this eval, where
text-only triage did as well as Due Process and the corpus let it judge by who owned the host. So read the table this way:

- The twin results are close to fixed by the design. Text-only triage gets almost the same input twice, so it can hardly
  answer differently. Due Process's abuse pages use techniques its signals are built to see.
- The total depends on the mix of cases. On the 26 original cases, text-only triage had 2 wrongful suspends of 14 and
  missed 0 of 12, against 1 of 14 and 2 of 12 for Due Process. On the 21 new cases it had 9 of 11 and 3 of 10, against
  0 of 11 and 0 of 10.
- Reading a report can be a good guess. What it cannot do is tell two pages apart, or give evidence for its decision.

**Browser against no browser:** the same rules with only an HTTP GET missed 13 of 22 abuse cases, against 2 of 22 with the
browser. Script redirects and script handlers are invisible to it.

**Containment, 10 runs:** the verdict stayed Proven 10 of 10, the cloud metadata request was blocked 10 of 10, and the
download was quarantined 10 of 10. The vision model repeated the page's "safe" claim 10 of 10, and the page-text flag
caught all 10.

Across 180 model outputs, none cited a signal that does not exist. Text-only triage is about 8 times faster and cheaper
per case: 2.89 s and USD 0.0003, against 23.69 s and USD 0.0026.

## Known limits

- **Iframes.** Only the top frame is analyzed, so a login inside a frame is missed (`phish-iframe` in the eval).
- **URLs built at run time.** A form's destination is read from its handler's source, and handlers are never run, so an
  assembled URL or a helper function outside the handler is missed (`phish-runtime-url`).
- **Single sign-on.** A legitimate login that posts to a third-party identity provider looks like phishing and comes out
  Proven (`sso-third-party`). An analyst approves every action for this reason. On Test any URL, where no one approves
  anything, that verdict is labeled as not a finding of abuse.
- **B3 depends on Docker's `docker-default` AppArmor profile.** Chromium's sandbox works on Ubuntu 24.04 because the
  container runs under that profile, as shown in [docs/FINDINGS.md](docs/FINDINGS.md). A Docker or AppArmor change could
  break it. The failure is safe: a visit with no namespace proof comes back Not proven.
- **Vision claims can repeat page text.** This happens, and it is flagged. It never moves a verdict.
- **Citation checks prove that a signal id exists**, not that the sentence describes it truly.
- **The check for uncited findings is a word list.** It can miss a finding in other words, and it can refuse a harmless
  sentence; then the documents fall back to a template from code.
- **The demo is open.** Anyone can approve simulated actions there. The launcher is still root-equivalent on its host
  if compromised.

## Prior work

The non-AI sandbox scaffolding comes from a probe built on November 1, before the hackathon started. It is kept unchanged
in [prior-work/](prior-work/), and [docs/prior-work.md](docs/prior-work.md) lists every file that came from it and how
it changed. The organizers allow this if disclosed. All AI functionality and the web app were built during the hackathon.

## License

MIT
