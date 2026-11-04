# Public deploy: status and blockers

The app port binds to `127.0.0.1` unless `APP_BIND` is set. Set it only when the items below are done.

## Done

- The app no longer holds the Docker socket. Only `launcher` does, and it offers `POST /visit` and nothing else. It has
  no port and is on an internal network with the app alone.
- The sandbox reaches the outside only through the egress proxy.
- Reports are rate limited per address, and the queue has a limit.

## Must do on the VM

1. **Set `EGRESS_DENY_IPS` to the VM's public address.** That address is public, so the proxy would otherwise allow it,
   and a page could reach the app from outside like any visitor.
2. **Check B3 on the VM.** Ubuntu 23.10 and later restrict unprivileged user namespaces, which makes Chromium's sandbox
   fail. See `prior-work/FINDINGS.md`. Cases then come back not ok and Not proven, so this fails safely, but the demo
   would not work.
3. **Check the client address.** The rate limit uses the socket address. On Docker Desktop all clients share one address.
   Confirm on the VM that the app sees real addresses; otherwise all visitors share one limit.
4. **Add TLS.** The app serves plain HTTP. Anything placed in front of it changes the client address in item 3.
5. **Cap the Vultr key's use.** The app stops at USD 5 of model cost, and new model calls are then refused and shown as
   skipped. Decide what the demo should do at that point during judging.

## Decisions still open

- **Public modes are a proposal.** With demo mode open, anyone can approve actions; they are simulated.
- **Test any URL shows screenshots of any page to every visitor** in the public queue. Consider hiding other people's
  cases, and showing a verdict there only with care: a legitimate login that posts to another domain, such as single
  sign-on, comes out Proven. The page says it is not a finding of abuse.

## Remaining risk

- `launcher` is still root-equivalent on the host if it is compromised. What changed is that only one small service with
  one route holds that power, and the internet cannot reach it.
- A sandbox escape past Chromium's sandbox and the container limits is not covered by the proxy.
- The rate limit lives in memory and resets when the app restarts.
