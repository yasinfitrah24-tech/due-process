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

- Nothing runs in the page's JS context any more. The probe's init script and `page.evaluate` are gone, because a
  page can rewrite both. Requests come from Playwright's network events, listeners and their source from CDP
  (`DOMDebugger.getEventListeners`, `Debugger.getScriptSource`), and the DOM from an isolated world.
- The regex over all page scripts is gone. Only the source of handlers bound to a form is read.
- Every evidence item has `kind`: `observed` or `declared_in_script`.
- Evidence fields follow schema `dp-visit/4`; the visit itself stays one fixed sequence.
- Namespace proof is recorded on every visit, not only with `--launch-only`.
- The last-resort alarm moved from 20 s to 5 s after the visit budget, so it fires before the launcher's hard kill.
- Removed the `--channel` option, which was for local testing.
