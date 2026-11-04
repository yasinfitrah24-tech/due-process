"""The app's only way to a sandbox: visit(url) on the launcher. The app has no Docker socket and no Docker client."""
import base64
import json
import os
from pathlib import Path

import httpx

MAX_PARALLEL = 4
TIMEOUT_S = 300  # a visit may wait for a free sandbox slot first


def run_visit(url, case, on_event=None):
    """Reads the launcher's stream: on_event gets each event as it happens, and the last line is the result."""
    base = os.environ.get("LAUNCHER_URL", "http://launcher:8100")
    with httpx.stream("POST", f"{base}/visit", json={"url": url, "case": case}, timeout=TIMEOUT_S) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            body = json.loads(line)
            if "result" in body:
                return body["result"]
            if on_event:
                on_event(body)
    raise RuntimeError("the launcher ended without a result")


def simulate_fix(slug):
    """Asks the test site's internal port to clean one fixable page. Nothing else can be changed there."""
    base = os.environ.get("TESTSITE_CONTROL_URL", "http://testsite:8081")
    httpx.post(f"{base}/fix/{slug}", timeout=10).raise_for_status()


def save(result, out_dir):
    """Writes evidence.json, receipt.json and shot-N.jpg. Screenshots leave the JSON here."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence = result["evidence"] or {}
    for i, page in enumerate(evidence.get("pages", [])):
        b64 = (page.get("screenshot") or {}).pop("jpeg_b64", None)
        if b64:
            (out_dir / f"shot-{i}.jpg").write_bytes(base64.b64decode(b64))
            page["screenshot"]["file"] = f"shot-{i}.jpg"
    (out_dir / "evidence.json").write_text(json.dumps(evidence, indent=2))
    (out_dir / "receipt.json").write_text(json.dumps(result["receipt"], indent=2))
