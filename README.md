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

From `make eval`, 26 labeled cases on fictional test pages, one run each, model cost USD 0.10. The full results, with
every case named, are in [docs/eval.md](docs/eval.md).

| Approach | Wrongful suspend | Abuse missed | Injection changed the outcome |
|---|---|---|---|
| Due Process | 1 of 14 | 2 of 12 | 0 of 4 |
| Text-only triage | 1 of 14 | 0 of 12 | 0 of 4 |
| Fetch without browser | 1 of 14 | 9 of 12 | not applicable: it never reads the report |

- **Against fetching without a browser, the browser is the difference**: the same rules with no browser missed 9 of 12
  abuse cases against 2 of 12.
- **Text-only triage did as well or better on this corpus, and this does not show Due Process beating it.** The corpus
  has a confound that may help it: abuse and customer are correlated, and the model is given the customer list. What it
  cannot give is evidence for its decision.
- Across 96 model outputs, none cited a signal that does not exist.
- Containment, 10 runs: the level stayed Proven 10 of 10, the metadata request was blocked 10 of 10 and the download
  was quarantined 10 of 10. The vision model repeated the page's "safe" claim 10 of 10, and the flag caught all 10.

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
- **The demo is open.** Anyone can approve simulated actions there. The launcher is still root-equivalent on its host
  if compromised.

## Prior work

The non-AI sandbox scaffolding comes from a probe built on November 1, before the hackathon started. It is kept unchanged
in [prior-work/](prior-work/), and [docs/prior-work.md](docs/prior-work.md) lists every file that came from it and how
it changed. The organizers allow this if disclosed. All AI functionality and the web app were built during the hackathon.

## License

MIT
