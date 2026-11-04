# Findings

Findings from the deployed system. The pre-hackathon probe has its own in `prior-work/FINDINGS.md`.

## Why B3 passes with `apparmor_restrict_unprivileged_userns=1`

Measured on the VM on 2026-11-04: Ubuntu 24.04.5, kernel 6.8.0-147, Docker 29.1.3, AppArmor parser 4.0.1.

**Answer:** that setting restricts unconfined processes. Inside a sandbox, Chromium is not unconfined: Docker runs it
under the `docker-default` AppArmor profile, and creating a user namespace is allowed there. The prediction in
`prior-work/FINDINGS.md` that B3 would fail on Ubuntu 23.10 and later was wrong for this setup.

| Where | AppArmor label | `unshare -Ur` | Chromium with its sandbox on |
|---|---|---|---|
| Host, as `nobody` | `unconfined` | fails: `write failed /proc/self/uid_map: Operation not permitted` | not tried |
| Sandbox, contract flags | `docker-default (enforce)` | works | OK, 3 of 7 processes in new user, pid and net namespaces (renderer, zygote) |
| Diagnostic container, contract flags plus `--security-opt apparmor=unconfined` | `unconfined` | fails, same error | fails: "Chromium sandboxing failed!" |

The last row is the control. One change, leaving the profile, brings the failure back, and the kernel log then shows
`apparmor="DENIED" operation="capable" profile="unprivileged_userns" comm="unshare"`. On Ubuntu, an unconfined process
that creates a user namespace moves into that restrictive `unprivileged_userns` profile.

The diagnostic container ran once and was removed. Production flags and host settings did not change. The sysctl stayed
1, and `kernel.apparmor_restrict_unprivileged_unconfined` is 0.

**Not established:** which line of kernel or profile logic exempts `docker-default`. The table shows the behavior, not
that detail.

**What this means:**

- B3 depends on Docker applying `docker-default`. The contract flags do not set an AppArmor profile, so it is the
  default that does this. A change to that default, or a Docker or AppArmor update that adds user namespace rules to the
  profile, could break Chromium's sandbox.
- It fails safely. Without a working sandbox the visit has no namespace proof and comes back not ok, so Not proven.
- Run `deploy/b3_check.py` after any Docker, AppArmor or kernel update on the VM.

## Docker publishes ports past ufw

Docker writes its own NAT rules, so `ufw` does not guard a published port. What limits exposure is what is published.

Checked on the VM on 2026-11-04:

| Container | Published ports |
|---|---|
| `caddy` | 80, 443 |
| `app`, `launcher`, `dp-egress`, `testsite`, `collector` | none |

The host's NAT table holds rules for 80 and 443 only. Listening on the host: 22, 80, 443 and local-only DNS and
daemon sockets. The VM has no global IPv6 address.

From outside, a direct connection from a laptop to `45.76.241.186`:

| Port | Result |
|---|---|
| 22 | connects (SSH) |
| 80 | connects, answers 308 |
| 443 | connects, answers 400 to a plain HTTP probe |
| 2019, 2375, 2376, 3128, 8000, 8080, 8100, 9000 | timeout |

The ports of the app (8000), launcher (8100) and egress proxy (3128) are in that list. The test site listens only on
its container's port 80 on an internal network, so the public port 80 is Caddy. A check through a SOCKS proxy is not
valid for this: the proxy reports success before it connects, so every port looks open.
