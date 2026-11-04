# Prior work

The organizers allow non-AI scaffolding from before the hackathon if it is disclosed. Due Process uses a sandbox
probe built on Nov 1, before the hackathon started on Nov 3. The probe contains no AI functionality.

`prior-work/` holds the probe as it was, unchanged. Its results are in `prior-work/FINDINGS.md`.

## Files that come from the probe

| File in this repo | From | Changes |
|---|---|---|
| `sandbox/seccomp/playwright-v1.64.1.json` | `prior-work/seccomp/` | none (upstream Playwright profile) |
| `sandbox/seccomp/playwright-v1.64.1-chroot.json` | `prior-work/seccomp/` | none |
| `sandbox/seccomp/patch_chroot.py` | `prior-work/seccomp/` | none |
| `sandbox/Dockerfile` | `prior-work/runner/Dockerfile` | default for `PW_TAG` |
| `sandbox/visit.py` | `prior-work/runner/visit.py` | see below |
| `sandbox/launch.py` | docker flags and teardown check from `prior-work/run.sh` | rewritten in Python: per-case container, hard kill, receipt, parallel limit |
| `testsite/server.py` | structure of `prior-work/testsite/server.py` | new pages, Northwind Bank fictional brand, routing by host |
| `testsite/collector.py` | `prior-work/echo/server.py` | logs method and path only, never reads the body |
| `tests/test_sandbox_e2e.py` | checks from `prior-work/check.py`, `run.sh isolation` | rewritten as pytest |

## Changes to `visit.py` during the hackathon

- Form destinations no longer come from a regex over all page scripts. The init script now records the source of
  submit and click listeners, and only the handlers bound to a form are read.
- Calls that really happen (fetch, XHR, sendBeacon, form.submit) are reported as `runtime:*` destinations.
- Namespace proof is recorded on every visit, not only with `--launch-only`.
- The last-resort alarm moved from 20 s to 5 s after the visit budget, so it fires before the launcher's hard kill.
- Removed the `--channel` option, which was for local testing.
