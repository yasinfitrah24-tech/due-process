# Public deploy: status and remaining risk

How to deploy is in `docs/deploy.md`.

## Done

- The app holds no Docker socket. Only `launcher` does, and it offers `POST /visit` and nothing else. It has no port and
  is on an internal network with the app alone.
- The sandbox reaches the outside only through the egress proxy.
- Only Caddy publishes ports. The app takes the client address from Caddy's header, and from nobody else.
- `EGRESS_DENY_IPS` holds the VM's public address.
- Reports are rate limited per address, and the queue has a limit.
- Model cost stops at USD 15 in total and USD 2 a day. Past that, model roles show as skipped and documents fall back to
  a template from code. Verdicts do not depend on models, so the demo keeps working.
- Test any URL cases are not listed anywhere and open only through a random link.

## Remaining risk

- `launcher` is still root-equivalent on the host if it is compromised. What changed is that one small service with one
  route holds that power, and the internet cannot reach it.
- A sandbox escape past Chromium's sandbox and the container limits is not covered by the proxy.
- With demo mode open, anyone can approve demo actions. They are simulated and touch fictional customers only.
- A Test any URL verdict can be wrong for a real site: a legitimate login that posts to another domain, such as single
  sign-on, comes out Proven. The page says it is not a finding of abuse.
- Anyone with a Test any URL link can see that case. A link is not a login.
- The rate limit lives in memory and resets when the app restarts.
- A flagged `vision_claim` means the claim overlaps page text. The test page banner overlaps on most pages, so an
  analyst has to read which sentence was repeated.
