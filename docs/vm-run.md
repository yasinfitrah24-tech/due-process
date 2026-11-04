# VM run, 2026-11-04

Public demo: <https://45-76-241-186.sslip.io>. Commit `4e3028f`, Vultr Seattle, Ubuntu 24.04.5, 4 vCPU, 16 GB. Raw output:
`eval/results/vm-run-2026-11-04.json`, with the private Test any URL link removed.

## Sandbox

`deploy/b3_check.py`: OK, before the stack and again with it running. Chromium 156, 3 of 7 processes in new user, pid and
net namespaces (renderer, zygote). `kernel.apparmor_restrict_unprivileged_userns` stayed 1. No sysctl, no AppArmor
change, and no fallback was needed. Why B3 passes despite that setting is not known.

## Network, from inside a sandbox on the VM

`deploy/vm_netcheck.py`: PASS. The Vultr metadata address answers 200 from the VM host.

| Target | Direct | Through the proxy |
|---|---|---|
| `169.254.169.254` | no route | 403, also as decimal and with CONNECT |
| VM public address, ports 22, 80, 443 | no route | 403 (`denied address`) |
| `45-76-241-186.sslip.io` | not tried | 403 (`denied address`) |
| `app`, `launcher`, `caddy`, `testsite`, `collector` | name does not resolve | 403 for `app` and `launcher` |
| `1.1.1.1`, `8.8.8.8` | no route | not tried |
| Private ranges, loopback | not tried | 403 |
| Demo host | name does not resolve | allowed |

## From outside

- HTTPS 200 with a valid Let's Encrypt certificate; HTTP redirects to HTTPS.
- Ports 8000, 8100, 3128, 2375, 8080: no answer. Listening on the host: 22, 80, 443 only.
- `ufw`: 22, 80, 443.
- 13 reports with a forged `X-Forwarded-For`: 429 from the 11th. Another address was still served, so the app sees real
  client addresses.

## Six cases through the public URL

Run on the VM against the public URL. "Case" is report to ready, "Sandbox" is the container run.

| Page | Level | Target | Case s | Sandbox s | Model s | Calls | Cost USD | Documents |
|---|---|---|---|---|---|---|---|---|
| (a) | Proven | cust-1001 | 19.9 | 5.2 | 13.9 | 4 | 0.00349 | accepted |
| (b) | Not proven | cust-1002 | 15.7 | 3.7 | 11.9 | 4 | 0.00262 | accepted |
| (c) with injection | Not proven | cust-1002 | 13.2 | 3.7 | 8.9 | 4 | 0.00171 | accepted |
| (d) | Suspicious | cust-1001 | 21.3 | 3.7 | 17.1 | 5 | 0.00444 | accepted on retry |
| Containment | Proven | cust-1001 | 35.5 | 3.7 | 31.6 | 5 | 0.00662 | template |
| Test any URL, `example.com` | Not proven | none | not timed | 3.7 | 10.4 | 2 | 0.00097 | none |

The case and model times of the last two were taken while a second run was active by mistake, and the case time of
Test any URL was lost when that run ended. Case 7 in the queue is left from that second run.

- 0 citations of signals that do not exist. 11 letters checked by script: right result and action, no signal ids.
- Collector requests: 0. Sandbox containers left: 0. Key in container logs: 0 matches.
- Containment: first try ran out of tokens, second was rejected for a finding with no citation, so the code template
  was used. Its letters are correct and plain.
- Vision flag: on for (a) and containment, which repeat the injected text. Also on for (d), a page title, and `example.com`,
  a page sentence. Off for (b) and (c), where only the banner was repeated.
- Test any URL: 404 by case number, 200 by link, not in the queue, Suspend refused.

Ledger after this run: USD 0.024111 of USD 15 on the VM.

## Case time, before and after running model roles in parallel

Measured on the VM on 2026-11-04 against the public URL, from report to ready, one case at a time. Before: `1ff3175`, one
run. After: `700f952`, two runs. Few runs on three pages, so this shows a direction, not a distribution.

| Page | Before s | After, run 1 s | After, run 2 s |
|---|---|---|---|
| Phishing (`/start`) | 21.1 | 12.6 | 22.6 (retry) |
| Legitimate login | 13.9 | 13.6 (retry) | 9.3 |
| Containment | 34.4 (retry) | 16.9 | 15.9 |
| Mean | 23.1 | 14.4 | 15.9 |

"Retry" marks a case whose documents role needed a second call. A retry still runs after the first try, so it is the slowest
path. The range after the change is 9.3 to 22.6 s.
