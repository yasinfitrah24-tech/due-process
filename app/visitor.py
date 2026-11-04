"""The app's only way to a sandbox: visit(url) on the launcher. The app has no Docker socket and no Docker client."""
import base64
import json
import os
from pathlib import Path

import httpx

MAX_PARALLEL = 4
TIMEOUT_S = 300  # a visit may wait for a free sandbox slot first


def run_visit(url, case):
    base = os.environ.get("LAUNCHER_URL", "http://launcher:8100")
    r = httpx.post(f"{base}/visit", json={"url": url, "case": case}, timeout=TIMEOUT_S)
    r.raise_for_status()
    return r.json()


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
