# Architecture

Five long-running containers on one VM, plus one disposable sandbox container per case.

| Container | Networks | Holds | Purpose |
|---|---|---|---|
| `app` | default, `dp-control` | `.env`, case data | Web UI, API, signals, verdict level, model calls to Vultr |
| `launcher` | `dp-control` | Docker socket | `POST /visit` only: starts a sandbox with fixed flags, confirms teardown |
| `egress` | `dp-sandbox`, `dp-testnet`, `dp-outbound` | nothing secret | The only way out of the sandbox network; logs every request |
| `testsite`, `collector` | `dp-testnet` | nothing | Fictional test pages |
| sandbox, one per case | `dp-sandbox` | nothing | Chromium, under the flags in `CLAUDE.md`; removed after each visit |

`dp-sandbox`, `dp-testnet` and `dp-control` are internal: no route out. `dp-sandbox` has only the proxy on it, so a
sandbox can connect to the proxy and nothing else. The app has no socket and no Docker client.

## One case

1. The app takes a report. In demo mode the customer comes from the URL's host through the inventory.
2. The app calls `visit(url)` on the launcher. It chooses no flag, image or network.
3. The launcher starts a sandbox. Chromium uses the proxy. A hostile page can only do what the proxy allows.
4. The launcher reads this visit's lines from the proxy log, confirms the container is gone and returns evidence and a
   receipt. A visit without proxy lines, namespace proof or teardown is not ok, and gets Not proven.
5. The app computes signals and the level, and stores them.
6. Only then do the model roles run. Report text reaches claim extraction only. Their output is `model_claim`.
7. An analyst decides. On sending a letter, code adds the result sentence and removes the signal ids. Both versions are
   kept in the audit log.

## Appeals

After Suspend or Warn, the customer notice carries a link. An appeal opens the same fixed visit in a new container. What
the customer writes is stored and shown to the analyst, and reaches no model and no decision. Reinstate is allowed only if
the second visit's level is lower than the level at the action; a second visit that failed has no level, so only Keep is
possible. A lower level means the evidence was not reproduced, not that a page is clean: a page can cloak.

## Live progress and timeline

`visit.py` prints an event as each thing happens, the launcher follows the proxy log during the visit, and `POST /visit`
answers with a stream of those events and then the result. The Case screen shows them as they arrive and keeps them as the
timeline. Proxy lines followed live are replaced by the proxy's log when the visit ends. Each event carries the time its
own source saw it, so the order of events a few milliseconds apart can differ from the order they happened in.

## Egress rules

- Allowed: addresses on the public internet, ports 80 and 443.
- Blocked: private and local ranges, 169.254.169.254, names such as `app` and `launcher`, any other port, and any
  address in `EGRESS_DENY_IPS`.
- Exception: the exact test hostnames in `EGRESS_ALLOW_PRIVATE_HOSTS`.
- The proxy connects to the address it checked, so a name cannot change between the check and the connection.
- For HTTPS the proxy sees only the host and port, not the path.

## Evidence kinds

| Kind | Meaning |
|---|---|
| `observed` | The browser or the proxy saw it |
| `declared_in_script` | Read from script source, never executed |
| `model_claim` | Written by a model. Never moves a level, a target or an action |

Only `form_to_foreign_host` and `password_field` decide the level.

## Public modes (proposal, not final)

| | Demo customers | Test any URL |
|---|---|---|
| URL | Host must be in the fictional inventory | Any http or https URL |
| Target | From the inventory | None |
| Actions | Simulated; anyone can approve | None; refused by the API |
| Model roles | All four | Vision and verdict claims |

Both share a rate limit of 10 reports per 10 minutes per address, and refuse new work when 20 cases are waiting. At most
4 sandboxes run at once.
