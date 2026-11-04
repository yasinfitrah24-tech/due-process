"""Visit one URL in headless Chromium and print one JSON evidence record to stdout.

Never fills, clicks or submits anything on the page. Downloads are saved and hashed, never opened.
Exit codes: 0 visited, 2 browser failed to launch, 124 hard timeout.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import signal
import sys
import tempfile
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

# Records network sinks that page scripts actually call (we never trigger handlers ourselves).
INIT_JS = r"""
(() => {
  const calls = [];
  Object.defineProperty(window, '__dpCalls', {value: calls});
  const rec = (kind, url, method) => {
    let u = String(url);
    try { u = String(new URL(url, location.href)); } catch (e) {}
    calls.push({kind, url: u, method: method ? String(method).toUpperCase() : null});
  };
  const of = window.fetch;
  window.fetch = function (input, init) {
    rec('fetch', (input && input.url) || input, (init && init.method) || (input && input.method) || 'GET');
    return of.apply(this, arguments);
  };
  const oo = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (m, u) { rec('xhr', u, m); return oo.apply(this, arguments); };
  if (navigator.sendBeacon) {
    const ob = navigator.sendBeacon.bind(navigator);
    navigator.sendBeacon = (u, d) => { rec('beacon', u, 'POST'); return ob(u, d); };
  }
  const os = HTMLFormElement.prototype.submit;
  HTMLFormElement.prototype.submit = function () { rec('form.submit', this.action, this.method); return os.apply(this, arguments); };
})();
"""

ANALYZE_JS = r"""
() => {
  const forms = [...document.forms].map((f, i) => ({
    index: i,
    id: f.id || null,
    action_attr: f.getAttribute('action'),
    action_resolved: f.hasAttribute('action') ? f.action : null,
    method: (f.getAttribute('method') || 'get').toLowerCase(),
    fields: [...f.elements].filter(e => e.tagName !== 'FIELDSET').map(e => ({
      tag: e.tagName.toLowerCase(), type: e.type || null, name: e.name || null, id: e.id || null})),
    has_password: !!f.querySelector('input[type=password]'),
  }));
  const password_fields = [...document.querySelectorAll('input[type=password]')].map(e => ({
    name: e.name || null, id: e.id || null, form_index: e.form ? [...document.forms].indexOf(e.form) : null}));

  const hidden_text = [];
  const skip = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'HEAD']);
  const walk = (el) => {
    for (const c of el.children) {
      if (skip.has(c.tagName)) continue;
      const cs = getComputedStyle(c);
      const r = c.getBoundingClientRect();
      let reason = null;
      if (cs.display === 'none') reason = 'display:none';
      else if (cs.visibility === 'hidden') reason = 'visibility:hidden';
      else if (parseFloat(cs.opacity) === 0) reason = 'opacity:0';
      else if ((r.width === 0 || r.height === 0) && cs.overflow === 'hidden') reason = 'zero-size';
      else if (r.right <= 0 || r.bottom <= 0) reason = 'offscreen';
      if (reason) {
        const t = (c.textContent || '').trim();
        if (t) hidden_text.push({tag: c.tagName.toLowerCase(), id: c.id || null, reason, text: t.slice(0, 1000)});
        continue;
      }
      walk(c);
    }
  };
  if (document.body) walk(document.body);

  const inline_scripts = [...document.scripts].filter(s => !s.src).map(s => s.textContent.slice(0, 200000));
  const handlers = [];
  for (const el of document.querySelectorAll('*')) {
    for (const a of el.attributes) {
      if (a.name.startsWith('on') || (a.value || '').trim().toLowerCase().startsWith('javascript:'))
        handlers.push({tag: el.tagName.toLowerCase(), attr: a.name, value: a.value.slice(0, 5000)});
    }
  }
  return {title: document.title, forms, password_fields, hidden_text, inline_scripts, handlers,
          runtime_calls: window.__dpCalls ? [...window.__dpCalls] : []};
}
"""

# fetch('u'), xhr.open('POST','u'), navigator.sendBeacon('u'), $.post('u'), axios.post('u')
SINK_RE = re.compile(
    r"""\b(fetch|open|sendBeacon|\$\.(?:post|get|ajax)|axios(?:\.\w+)?)\s*\(\s*"""
    r"""(?:(['"])[A-Za-z]+\2\s*,\s*)?(['"`])([^'"`\s]+?)\3""")
ABS_URL_RE = re.compile(r"""https?://[^\s'"`<>()\\]+""")


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def host(u):
    try:
        return urlsplit(u).hostname
    except ValueError:
        return None


def script_targets(page_url, sources):
    """Static scan of script text for network sinks and absolute URLs. sources: [(label, text)]"""
    out, seen = [], set()
    for label, text in sources:
        for m in SINK_RE.finditer(text):
            u = urljoin(page_url, m.group(4))
            key = (m.group(1), u, label)
            if key not in seen:
                seen.add(key)
                out.append({"via": m.group(1), "url": u, "source": label})
        for m in ABS_URL_RE.finditer(text):
            u = m.group(0).rstrip(".,;")
            if not any(t["url"] == u for t in out):
                out.append({"via": "literal-url", "url": u, "source": label})
    return out


def read_cgroup_peak():
    for p in ("/sys/fs/cgroup/memory.peak", "/sys/fs/cgroup/memory/memory.max_usage_in_bytes"):
        try:
            with open(p) as f:
                return {"file": p, "bytes": int(f.read().strip())}
        except (OSError, ValueError):
            pass
    return None


def read_cgroup_cpu_s():
    """CPU time of the whole container (cgroup v2) so far, in seconds."""
    try:
        with open("/sys/fs/cgroup/cpu.stat") as f:
            for line in f:
                k, v = line.split()
                if k == "usage_usec":
                    return int(v) / 1e6
    except (OSError, ValueError):
        pass
    return None


def sandbox_evidence():
    """chrome://sandbox is not served by headless shell (ERR_INVALID_URL), so compare namespaces instead:
    with the sandbox on, zygote and renderers run in a new user + pid + net namespace."""
    def ns(pid, kind):
        try:
            return os.readlink(f"/proc/{pid}/ns/{kind}")
        except OSError:
            return None

    kinds = ("user", "pid", "net")
    mine = {k: ns("self", k) for k in kinds}
    procs = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if "chrome" not in cmd.split(" ", 1)[0]:
            continue
        m = re.search(r"--type=([\w-]+)", cmd)
        procs.append({"pid": int(pid), "type": m.group(1) if m else "browser",
                      **{f"new_{k}_ns": ns(pid, k) not in (None, mine[k]) for k in kinds}})
    return {"chromium_processes": len(procs),
            "in_new_user_pid_net_ns": sum(all(p[f"new_{k}_ns"] for k in kinds) for p in procs),
            "processes": procs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?")
    ap.add_argument("--timeout", type=float, default=60, help="whole-visit budget in seconds")
    ap.add_argument("--settle", type=float, default=3, help="quiet seconds without navigation before stopping")
    ap.add_argument("--sandbox", choices=["on", "off"], default="on", help="Chromium's own sandbox")
    ap.add_argument("--launch-only", action="store_true", help="launch, report sandbox status, exit")
    ap.add_argument("--no-embed", action="store_true", help="omit screenshot base64 from JSON")
    ap.add_argument("--workdir", default=os.path.join(tempfile.gettempdir(), "dp"))
    ap.add_argument("--channel", help="local testing only, e.g. msedge; the container uses bundled Chromium")
    a = ap.parse_args()

    t0 = time.monotonic()
    rec = {
        "probe": "dp-visit/1",
        "input_url": a.url,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "timeout_s": a.timeout,
        "browser": {"sandbox_requested": a.sandbox},
        "errors": [],
        "env_keys": sorted(os.environ),
    }

    def emit_and_exit(code):
        rec["timing"] = {**rec.get("timing", {}), "total_s": round(time.monotonic() - t0, 3)}
        rec["cgroup_memory_peak"] = read_cgroup_peak()
        rec["cgroup_cpu_s"] = read_cgroup_cpu_s()
        sys.stdout.write(json.dumps(rec) + "\n")
        sys.stdout.flush()
        os._exit(code)

    # Last-resort guard in case the browser itself wedges past the visit budget.
    if hasattr(signal, "SIGALRM"):
        def on_alarm(*_):
            rec["errors"].append({"stage": "hard-timeout", "message": f"exceeded {a.timeout + 20}s"})
            rec["timed_out"] = True
            emit_and_exit(124)
        signal.signal(signal.SIGALRM, on_alarm)
        signal.alarm(int(a.timeout + 20))

    os.makedirs(os.path.join(a.workdir, "downloads"), exist_ok=True)

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True, chromium_sandbox=(a.sandbox == "on"), channel=a.channel)
        except PWError as e:
            rec["browser"]["launch_error"] = str(e)[:4000]
            emit_and_exit(2)
        rec["browser"]["version"] = browser.version
        rec["timing"] = {"launch_s": round(time.monotonic() - t0, 3)}

        ctx = browser.new_context(accept_downloads=True, service_workers="block",
                                  viewport={"width": 1280, "height": 800})

        if a.launch_only:
            p = ctx.new_page()
            p.set_content("<p>TEST PAGE</p>")  # makes sure a renderer exists
            rec["browser"]["sandbox_evidence"] = sandbox_evidence()
            browser.close()
            emit_and_exit(0)

        ctx.add_init_script(INIT_JS)
        page = ctx.new_page()

        requests, status_by_req, script_resps = [], {}, []
        nav_reqs, pages, downloads = [], [], []
        last_nav = [time.monotonic()]

        def on_request(r):
            requests.append(r)
            if r.is_navigation_request() and r.frame == page.main_frame:
                nav_reqs.append(r)
                last_nav[0] = time.monotonic()

        def on_response(resp):
            status_by_req[id(resp.request)] = resp.status
            if resp.request.resource_type == "script":
                script_resps.append(resp)

        def snapshot(p):
            entry = {"url": p.url}
            try:
                img = p.screenshot(type="jpeg", quality=70, timeout=10000)
                entry["screenshot"] = {"bytes": len(img), "sha256": sha256(img)}
                if not a.no_embed:
                    entry["screenshot"]["jpeg_b64"] = base64.b64encode(img).decode()
            except PWError as e:
                entry["screenshot_error"] = str(e)[:300]
            try:
                entry["analysis"] = p.evaluate(ANALYZE_JS)
            except PWError as e:
                entry["analysis_error"] = str(e)[:300]
            return entry

        def on_load(p):
            pages.append(snapshot(p))

        page.on("request", on_request)
        page.on("response", on_response)
        page.on("download", lambda d: downloads.append(d))
        page.on("load", on_load)

        deadline = t0 + a.timeout
        rec["timed_out"] = False
        tv = time.monotonic()
        try:
            page.goto(a.url, wait_until="load", timeout=max(1, deadline - time.monotonic()) * 1000)
            # Follow client-side redirects until no new main-frame navigation for `settle` seconds.
            while time.monotonic() < deadline:
                page.wait_for_timeout(250)
                if time.monotonic() - last_nav[0] >= a.settle:
                    break
            page.wait_for_load_state("load", timeout=max(1, deadline - time.monotonic()) * 1000)
        except PWTimeout as e:
            rec["timed_out"] = True
            rec["errors"].append({"stage": "visit", "message": str(e).splitlines()[0][:300]})
        except PWError as e:
            rec["errors"].append({"stage": "visit", "message": str(e)[:500]})
        rec["timing"]["visit_s"] = round(time.monotonic() - tv, 3)

        # Fresh analysis of the page as it stands now (scripts may have changed it after load).
        final = snapshot(page) if not rec["timed_out"] else {"url": page.url}
        if pages and pages[-1]["url"] == final["url"]:
            pages[-1] = final
        elif not rec["timed_out"]:
            pages.append(final)
        rec["final_url"] = page.url

        # Downloads: save under workdir, hash, never open.
        dls, dl_urls = [], set()
        for i, d in enumerate(downloads):
            item = {"url": d.url, "suggested_filename": d.suggested_filename}
            dl_urls.add(d.url)
            try:
                safe = re.sub(r"[^A-Za-z0-9._-]", "_", d.suggested_filename)[:100] or "download"
                path = os.path.join(a.workdir, "downloads", f"{i}-{safe}")
                d.save_as(path)
                with open(path, "rb") as f:
                    data = f.read()
                item.update(saved_to=path, bytes=len(data), sha256=sha256(data))
            except PWError as e:
                item["error"] = str(e)[:300]
            dls.append(item)
        rec["downloads"] = dls

        # Redirect chain: HTTP redirects carry redirected_from; anything else after the first is client-side.
        chain = [r for r in nav_reqs if r.url not in dl_urls]
        redirects, visited = [], []
        for r in chain:
            prev = r.redirected_from
            if prev is not None:
                redirects.append({"type": "http", "status": status_by_req.get(id(prev)),
                                  "from": prev.url, "to": r.url})
            elif visited:
                redirects.append({"type": "client", "from": visited[-1], "to": r.url})
            visited.append(r.url)
        rec["redirects"] = redirects
        rec["navigations"] = visited

        # Per-page evidence and aggregated findings.
        ext_sources = []
        for resp in script_resps:
            try:
                ext_sources.append((resp.url, resp.body()[:1_000_000].decode("utf-8", "replace")))
            except PWError:
                pass
        rec["pages"], rec["forms"], rec["password_fields"], rec["hidden_text"] = [], [], [], []
        rec["destinations"], rec["runtime_calls"] = [], []
        for pg in pages:
            an = pg.pop("analysis", None) or {}
            u = pg["url"]
            pg["title"] = an.get("title")
            rec["pages"].append(pg)
            for f in an.get("forms", []):
                rec["forms"].append({"page": u, **f})
                if f["action_resolved"]:
                    rec["destinations"].append({"kind": "form-action", "page": u, "url": f["action_resolved"],
                                                "method": f["method"], "form_index": f["index"]})
            rec["password_fields"] += [{"page": u, **p} for p in an.get("password_fields", [])]
            rec["hidden_text"] += [{"page": u, **h} for h in an.get("hidden_text", [])]
            rec["runtime_calls"] += [{"page": u, **c} for c in an.get("runtime_calls", [])]
            sources = [(f"inline-script#{i}", s) for i, s in enumerate(an.get("inline_scripts", []))]
            sources += [(f"{h['tag']}[{h['attr']}]", h["value"]) for h in an.get("handlers", [])]
            sources += [(su, st) for su, st in ext_sources if host(su) == host(u)]
            for t in script_targets(u, sources):
                rec["destinations"].append({"kind": f"script:{t['via']}", "page": u, "url": t["url"],
                                            "source": t["source"]})
        for d in rec["destinations"]:
            d["host"] = host(d["url"])
            d["same_host_as_page"] = d["host"] == host(d["page"])

        rec["network"] = [{
            "method": r.method, "url": r.url, "resource_type": r.resource_type,
            "navigation": r.is_navigation_request(), "status": status_by_req.get(id(r)),
            "failure": r.failure,
        } for r in requests]

        try:
            browser.close()
        except PWError:
            pass
    emit_and_exit(0)


if __name__ == "__main__":
    main()
