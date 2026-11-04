"""Visit one URL in headless Chromium and print one JSON evidence record to stdout.

Never fills, clicks or submits anything on the page. Downloads are saved and hashed, never opened.
The visit is fixed: open, follow redirects, screenshot. Nothing from a report or a model shapes it.
Exit codes: 0 visited, 2 browser failed to launch, 124 hard timeout.

Evidence must not depend on anything the page can change, so nothing here runs in the page's JS context:
  - requests come from Playwright's network events (browser side);
  - listeners and their source come from CDP (DOMDebugger.getEventListeners, Debugger.getScriptSource);
  - the DOM is read from an isolated world, which shares the DOM but not the page's globals or prototypes.
Every evidence item has kind "observed" (the browser saw it) or "declared_in_script" (read from script
source, never executed: a form that only sends on submit never sends here, because nothing is submitted).
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

OBSERVED = "observed"
DECLARED = "declared_in_script"

HANDLER_SOURCE_MAX = 20000

# Runs in an isolated world. A page can still clobber properties of forms and of document with named elements
# (<input name="action">, <img name="forms">), which reach every world, so those go through the prototype getters.
ANALYZE_JS = r"""
(() => {
  const getter = (proto, name) => {
    const g = Object.getOwnPropertyDescriptor(proto, name).get;
    return (o) => g.call(o);
  };
  const attr = (e, n) => Element.prototype.getAttribute.call(e, n);
  const hasAttr = (e, n) => Element.prototype.hasAttribute.call(e, n);
  const within = (e, s) => [...Element.prototype.querySelectorAll.call(e, s)];
  const all = (s) => [...Document.prototype.querySelectorAll.call(document, s)];
  const children = getter(Element.prototype, 'children');
  const tagName = getter(Element.prototype, 'tagName');
  const textContent = getter(Node.prototype, 'textContent');
  const elements = getter(HTMLFormElement.prototype, 'elements');
  const baseURI = getter(Node.prototype, 'baseURI')(document);
  const resolve = (u) => { try { return new URL(u, baseURI).href; } catch (e) { return u; } };

  const formEls = all('form');
  const forms = formEls.map((f, i) => ({
    index: i,
    id: attr(f, 'id'),
    action_attr: attr(f, 'action'),
    action_resolved: hasAttr(f, 'action') ? resolve(attr(f, 'action')) : null,
    button_actions: within(f, '[formaction]').map(b => resolve(attr(b, 'formaction'))),
    method: (attr(f, 'method') || 'get').toLowerCase(),
    fields: [...elements(f)].filter(e => tagName(e) !== 'FIELDSET').map(e => ({
      tag: tagName(e).toLowerCase(), type: attr(e, 'type'), name: attr(e, 'name'), id: attr(e, 'id')})),
    has_password: within(f, 'input[type=password]').length > 0,
  }));
  const password_fields = all('input[type=password]').map(e => ({
    name: attr(e, 'name'), id: attr(e, 'id'), form_index: e.form ? formEls.indexOf(e.form) : null}));

  const hidden_text = [];
  const skip = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'HEAD']);
  const walk = (el) => {
    for (const c of children(el)) {
      if (skip.has(tagName(c))) continue;
      const cs = getComputedStyle(c);
      const r = Element.prototype.getBoundingClientRect.call(c);
      let reason = null;
      if (cs.display === 'none') reason = 'display:none';
      else if (cs.visibility === 'hidden') reason = 'visibility:hidden';
      else if (parseFloat(cs.opacity) === 0) reason = 'opacity:0';
      else if ((r.width === 0 || r.height === 0) && cs.overflow === 'hidden') reason = 'zero-size';
      else if (r.right <= 0 || r.bottom <= 0) reason = 'offscreen';
      if (reason) {
        const t = (textContent(c) || '').trim();
        if (t) hidden_text.push({tag: tagName(c).toLowerCase(), id: attr(c, 'id'), reason, text: t.slice(0, 1000)});
        continue;
      }
      walk(c);
    }
  };
  const body = getter(Document.prototype, 'body')(document);
  if (body) walk(body);
  return {title: getter(Document.prototype, 'title')(document), forms, password_fields, hidden_text};
})()
"""

# fetch('u'), xhr.open('POST','u'), navigator.sendBeacon('u'), $.post('u'), axios.post('u')
SINK_RE = re.compile(
    r"""\b(fetch|open|sendBeacon|\$\.(?:post|get|ajax)|axios(?:\.\w+)?)\s*\(\s*"""
    r"""(?:(['"])[A-Za-z]+\2\s*,\s*)?(['"`])([^'"`\s]+?)\3""")
ABS_URL_RE = re.compile(r"""https?://[^\s'"`<>()\\]+""")
ALARM_MARGIN_S = 5  # past the visit budget; still inside the launcher's hard kill
SENDING_TYPES = {"fetch", "xhr", "ping", "eventsource"}


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def host(u):
    try:
        return urlsplit(u).hostname
    except ValueError:
        return None


def script_targets(page_url, sources):
    """Network sinks and absolute URLs in the source of a form's own handlers. sources: [(label, text)]"""
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


class Inspector:
    """Reads a page from outside its JS context, over one CDP session."""

    def __init__(self, ctx, page):
        self.cdp = ctx.new_cdp_session(page)
        self.scripts = {}
        self.cdp.on("Debugger.scriptParsed", lambda e: self.scripts.__setitem__(e["scriptId"], e))
        for domain in ("Runtime", "Page", "DOM", "Debugger"):
            self.cdp.send(f"{domain}.enable")
        # A `debugger` statement must not freeze the visit.
        self.cdp.send("Debugger.setSkipAllPauses", {"skip": True})

    def analyze(self):
        frame = self.cdp.send("Page.getFrameTree")["frameTree"]["frame"]["id"]
        world = self.cdp.send("Page.createIsolatedWorld", {"frameId": frame, "worldName": "dp-evidence"})
        r = self.cdp.send("Runtime.evaluate", {"expression": ANALYZE_JS, "returnByValue": True,
                                               "contextId": world["executionContextId"]})
        if "exceptionDetails" in r:
            raise PWError(str(r["exceptionDetails"])[:300])
        an = r["result"]["value"]
        for f, handlers in zip(an["forms"], self.form_handlers()):
            f["handlers"] = handlers
        return an

    def listeners(self, object_id, depth):
        return self.cdp.send("DOMDebugger.getEventListeners",
                             {"objectId": object_id, "depth": depth, "objectGroup": "dp"})["listeners"]

    def handler_source(self, listener):
        """The handler's text out of its script. Locations count from the document, script text does not."""
        script = self.scripts.get(listener["scriptId"])
        description = (listener.get("handler") or {}).get("description", "")
        if not script:
            return description[:HANDLER_SOURCE_MAX]
        src = self.cdp.send("Debugger.getScriptSource", {"scriptId": listener["scriptId"]})["scriptSource"]
        row = listener["lineNumber"] - script["startLine"]
        col = listener["columnNumber"] - (script["startColumn"] if row == 0 else 0)
        start = sum(len(line) for line in src.splitlines(True)[:row]) + col
        return src[start:start + (len(description) or HANDLER_SOURCE_MAX)][:HANDLER_SOURCE_MAX]

    def form_handlers(self):
        """Per form, in document order: submit/click listeners on it and inside it, plus submit listeners on
        document and window, which see every form's submit."""
        root = self.cdp.send("DOM.getDocument", {"depth": 0})["root"]["nodeId"]
        forms = self.cdp.send("DOM.querySelectorAll", {"nodeId": root, "selector": "form"})["nodeIds"]
        document = self.cdp.send("DOM.resolveNode", {"nodeId": root, "objectGroup": "dp"})["object"]["objectId"]
        window = self.cdp.send("Runtime.evaluate", {"expression": "window", "objectGroup": "dp"})["result"]["objectId"]

        def entries(found, on, types):
            return [{"event": x["type"], "on": on, "source": self.handler_source(x)}
                    for x in found if x["type"] in types]

        shared = (entries(self.listeners(document, 0), "document", {"submit"})
                  + entries(self.listeners(window, 0), "window", {"submit"}))
        out = []
        for node in forms:
            obj = self.cdp.send("DOM.resolveNode", {"nodeId": node, "objectGroup": "dp"})["object"]["objectId"]
            out.append(entries(self.listeners(obj, -1), "form", {"submit", "click"}) + shared)
        self.cdp.send("Runtime.releaseObjectGroup", {"objectGroup": "dp"})
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?")
    ap.add_argument("--timeout", type=float, default=60, help="whole-visit budget in seconds")
    ap.add_argument("--settle", type=float, default=3, help="quiet seconds without navigation before stopping")
    ap.add_argument("--sandbox", choices=["on", "off"], default="on", help="Chromium's own sandbox")
    ap.add_argument("--launch-only", action="store_true", help="launch, report sandbox status, exit")
    ap.add_argument("--no-embed", action="store_true", help="omit screenshot base64 from JSON")
    ap.add_argument("--workdir", default=os.path.join(tempfile.gettempdir(), "dp"))
    a = ap.parse_args()

    t0 = time.monotonic()
    rec = {
        "schema": "dp-visit/4",
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
            rec["errors"].append({"stage": "hard-timeout", "message": f"exceeded {a.timeout + ALARM_MARGIN_S}s"})
            rec["timed_out"] = True
            emit_and_exit(124)
        signal.signal(signal.SIGALRM, on_alarm)
        signal.alarm(int(a.timeout + ALARM_MARGIN_S))

    os.makedirs(os.path.join(a.workdir, "downloads"), exist_ok=True)

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True, chromium_sandbox=(a.sandbox == "on"))
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

        page = ctx.new_page()
        inspector = Inspector(ctx, page)

        requests, status_by_req, page_of_req = [], {}, {}
        nav_reqs, pages, downloads = [], [], []
        last_nav = [time.monotonic()]

        def on_request(r):
            requests.append(r)
            page_of_req[id(r)] = page.url
            if r.is_navigation_request() and r.frame == page.main_frame:
                nav_reqs.append(r)
                last_nav[0] = time.monotonic()

        def on_response(resp):
            status_by_req[id(resp.request)] = resp.status

        def snapshot(p):
            entry = {"url": p.url}
            try:
                img = p.screenshot(type="jpeg", quality=70, timeout=10000)
                entry["screenshot"] = {"bytes": len(img), "sha256": sha256(img), "kind": OBSERVED}
                if not a.no_embed:
                    entry["screenshot"]["jpeg_b64"] = base64.b64encode(img).decode()
            except PWError as e:
                entry["screenshot_error"] = str(e)[:300]
            try:
                entry["analysis"] = inspector.analyze()
            except PWError as e:
                entry["analysis_error"] = str(e)[:300]
            return entry

        def record(entry):
            if pages and pages[-1]["url"] == entry["url"]:
                pages[-1] = entry
            else:
                pages.append(entry)

        ctx.on("request", on_request)
        ctx.on("response", on_response)
        page.on("download", lambda d: downloads.append(d))
        page.on("load", lambda p: record(snapshot(p)))

        deadline = t0 + a.timeout
        rec["timed_out"] = False
        tv = time.monotonic()

        def remaining_ms():
            return max(1, deadline - time.monotonic()) * 1000

        try:
            page.goto(a.url, wait_until="load", timeout=remaining_ms())
            # Follow client-side redirects until no new main-frame navigation for `settle` seconds.
            while time.monotonic() < deadline:
                page.wait_for_timeout(250)
                if time.monotonic() - last_nav[0] >= a.settle:
                    break
            page.wait_for_load_state("load", timeout=remaining_ms())
        except PWTimeout as e:
            rec["timed_out"] = True
            rec["errors"].append({"stage": "visit", "message": str(e).splitlines()[0][:300]})
        except PWError as e:
            rec["errors"].append({"stage": "visit", "message": str(e)[:500]})
        rec["timing"]["visit_s"] = round(time.monotonic() - tv, 3)
        rec["browser"]["sandbox_evidence"] = sandbox_evidence()

        # Fresh analysis of the page as it stands now (scripts may have changed it after load).
        if not rec["timed_out"]:
            record(snapshot(page))
        rec["final_url"] = page.url

        # Downloads: save under workdir, hash, never open.
        dls, dl_urls = [], set()
        for i, d in enumerate(downloads):
            item = {"url": d.url, "suggested_filename": d.suggested_filename, "kind": OBSERVED}
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
                                  "from": prev.url, "to": r.url, "kind": OBSERVED})
            elif visited:
                redirects.append({"type": "client", "from": visited[-1], "to": r.url, "kind": OBSERVED})
            visited.append(r.url)
        rec["redirects"] = redirects
        rec["navigations"] = visited

        # Per-page evidence and aggregated findings.
        rec["pages"], rec["forms"], rec["password_fields"], rec["hidden_text"] = [], [], [], []
        rec["destinations"] = []
        for pg in pages:
            an = pg.pop("analysis", None) or {}
            u = pg["url"]
            pg["title"] = an.get("title")
            rec["pages"].append(pg)
            for f in an.get("forms", []):
                rec["forms"].append({"page": u, "kind": OBSERVED, **f})
                actions = ([f["action_resolved"]] if f["action_resolved"] else []) + f["button_actions"]
                for action_url in actions:
                    rec["destinations"].append({"via": "form-action", "kind": OBSERVED, "page": u, "url": action_url,
                                                "method": f["method"], "form_index": f["index"]})
                sources = [(f"{h['on']}:{h['event']}", h["source"]) for h in f.get("handlers", [])]
                for t in script_targets(u, sources):
                    rec["destinations"].append({"via": f"form-handler:{t['via']}", "kind": DECLARED, "page": u,
                                                "url": t["url"], "source": t["source"], "form_index": f["index"]})
            rec["password_fields"] += [{"page": u, "kind": OBSERVED, **p} for p in an.get("password_fields", [])]
            rec["hidden_text"] += [{"page": u, "kind": OBSERVED, **h} for h in an.get("hidden_text", [])]

        # Requests that really left the page, from the browser's own network events.
        for r in requests:
            if r.resource_type in SENDING_TYPES:
                rec["destinations"].append({"via": f"request:{r.resource_type}", "kind": OBSERVED,
                                            "page": page_of_req[id(r)], "url": r.url, "method": r.method})
        for d in rec["destinations"]:
            d["host"] = host(d["url"])
            d["same_host_as_page"] = d["host"] == host(d["page"])

        rec["network"] = [{
            "method": r.method, "url": r.url, "resource_type": r.resource_type,
            "navigation": r.is_navigation_request(), "status": status_by_req.get(id(r)),
            "failure": r.failure, "kind": OBSERVED,
        } for r in requests]

        try:
            browser.close()
        except PWError:
            pass
    emit_and_exit(0)


if __name__ == "__main__":
    main()
