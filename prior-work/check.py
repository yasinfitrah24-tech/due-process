"""Host-side check of visit.py output against the probe's done-criteria. Also extracts screenshots.

usage: python3 check.py start|plain out/<name>.json [shots_dir]
"""
import base64
import hashlib
import json
import os
import sys

SAMPLE_SHA256 = hashlib.sha256(b"TEST PAGE sample download for the detonation probe.\n").hexdigest()


def main():
    kind, path = sys.argv[1], sys.argv[2]
    shots = sys.argv[3] if len(sys.argv) > 3 else None
    with open(path) as f:
        r = json.load(f)

    if shots:
        os.makedirs(shots, exist_ok=True)
        for i, p in enumerate(r.get("pages", [])):
            b64 = (p.get("screenshot") or {}).get("jpeg_b64")
            if b64:
                with open(os.path.join(shots, f"{kind}-{i}.jpg"), "wb") as f:
                    f.write(base64.b64decode(b64))

    checks = []

    def ok(name, cond, detail=""):
        checks.append(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))

    shot_urls = [p["url"] for p in r.get("pages", []) if p.get("screenshot")]
    sizes = [p["screenshot"]["bytes"] for p in r.get("pages", []) if p.get("screenshot")]
    print(f"{kind}: final={r.get('final_url')} visit_s={r.get('timing', {}).get('visit_s')} "
          f"screenshots={len(sizes)} jpeg_bytes={sizes}")
    ok("no visit errors", not r.get("errors"), json.dumps(r.get("errors"))[:200])

    if kind == "start":
        red = r.get("redirects", [])
        ok("HTTP redirect /start -> /step2",
           any(x["type"] == "http" and x["from"].endswith("/start") and x["to"].endswith("/step2") for x in red),
           json.dumps(red))
        ok("JS redirect /step2 -> /form",
           any(x["type"] == "client" and x["from"].endswith("/step2") and x["to"].endswith("/form") for x in red))
        ok("final URL is /form", str(r.get("final_url")).endswith("/form"))
        ok("screenshot of /step2 and /form",
           any(u.endswith("/step2") for u in shot_urls) and any(u.endswith("/form") for u in shot_urls), str(shot_urls))
        ok("fetch target echo.test/submit found without submitting",
           any(d["kind"].startswith("script:fetch") and d["url"] == "http://echo.test/submit"
               for d in r.get("destinations", [])))
        ok("fetch was NOT executed", not any("echo.test" in c["url"] for c in r.get("runtime_calls", []))
           and not any("echo.test" in n["url"] for n in r.get("network", [])))
        ok("password field", any(p.get("name") == "password" for p in r.get("password_fields", [])))
        ok("hidden text", any("secret-marker-42" in h["text"] for h in r.get("hidden_text", [])),
           json.dumps(r.get("hidden_text"))[:200])
        dl = r.get("downloads", [])
        ok("download sample.txt + sha256",
           any(d.get("suggested_filename") == "sample.txt" and d.get("sha256") == SAMPLE_SHA256 for d in dl),
           json.dumps([{k: d.get(k) for k in ("suggested_filename", "bytes", "sha256")} for d in dl]))
    elif kind == "plain":
        fa = [d for d in r.get("destinations", []) if d["kind"] == "form-action"]
        ok("form to same host", bool(fa) and all(d["same_host_as_page"] for d in fa), json.dumps(fa))
        ok("no hidden text", not r.get("hidden_text"), json.dumps(r.get("hidden_text"))[:200])
        ok("no downloads", not r.get("downloads"))
        ok("no password field", not r.get("password_fields"))

    sys.exit(0 if all(checks) else 1)


if __name__ == "__main__":
    main()
