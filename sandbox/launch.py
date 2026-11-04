"""Run one URL visit in a disposable container and return its evidence plus a sandbox receipt.

CLI: python -m sandbox.launch <url> [--case NAME] [--out DIR]
The flags in docker_args() are the sandbox contract in CLAUDE.md: don't change them without being asked.
"""
import argparse
import base64
import json
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

PW_VERSION = "v1.64.1"
IMAGE = f"due-process-sandbox:{PW_VERSION}"
NETWORK = "dp-sandbox"
SECCOMP = Path(__file__).resolve().parent / "seccomp" / f"playwright-{PW_VERSION}-chroot.json"
HARD_TIMEOUT_S = 60
VISIT_BUDGET_S = 53  # + visit.py's 5 s alarm margin + container start stays under HARD_TIMEOUT_S
MAX_PARALLEL = 4

_slots = threading.BoundedSemaphore(MAX_PARALLEL)


def now():
    return datetime.now(timezone.utc).isoformat()


def docker_args(name, cidfile):
    return ["docker", "run", "--name", name, "--cidfile", str(cidfile),
            "--network", NETWORK, "--user", "pwuser", "--read-only", "--tmpfs", "/tmp:rw,size=256m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--security-opt", f"seccomp={SECCOMP}",
            "--memory", "1g", "--memory-swap", "1g", "--pids-limit", "256", "--shm-size", "512m",
            "--cpus", "1", "--rm", IMAGE]


def visit_args(url):
    # "--" so a reported URL can never be read as an option by visit.py.
    return ["--sandbox", "on", "--timeout", str(VISIT_BUDGET_S), "--", url]


def check_url(url):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"not an http(s) URL: {url!r}")


def container_exists(cid):
    out = subprocess.run(["docker", "ps", "-aq", "--no-trunc", "--filter", f"id={cid}"],
                         capture_output=True, text=True, check=True).stdout
    return bool(out.strip())


def teardown(cid):
    """--rm removes the container shortly after exit. Confirm it, and force it if it is still there."""
    if not cid:
        return {"container_gone": True, "forced_removal": False, "checked_at": now(), "note": "never started"}
    for _ in range(20):
        if not container_exists(cid):
            return {"container_gone": True, "forced_removal": False, "checked_at": now()}
        time.sleep(0.25)
    subprocess.run(["docker", "rm", "-f", cid], capture_output=True)
    return {"container_gone": not container_exists(cid), "forced_removal": True, "checked_at": now()}


def namespace_proof(evidence):
    ev = (evidence or {}).get("browser", {}).get("sandbox_evidence") or {}
    isolated = [p for p in ev.get("processes", [])
                if p["new_user_ns"] and p["new_pid_ns"] and p["new_net_ns"]]
    return {"chromium_processes": ev.get("chromium_processes", 0),
            "in_new_user_pid_net_ns": ev.get("in_new_user_pid_net_ns", 0),
            "isolated_types": sorted({p["type"] for p in isolated})}


def run_visit(url, case="case"):
    """Returns {"ok", "evidence", "receipt"}. ok means: evidence came back, Chromium's sandbox was
    really on (processes in new namespaces), and the container is confirmed gone."""
    check_url(url)
    name = f"dp-case-{re.sub(r'[^a-z0-9-]', '-', case.lower())[:40]}-{uuid.uuid4().hex[:8]}"
    with _slots, tempfile.TemporaryDirectory() as tmp:
        cidfile = Path(tmp) / "cid"
        flags = docker_args(name, cidfile)
        started_at, t0, hard_killed = now(), time.monotonic(), False
        proc = subprocess.Popen(flags + visit_args(url), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, err = proc.communicate(timeout=HARD_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            hard_killed = True
            subprocess.run(["docker", "kill", name], capture_output=True)
            out, err = proc.communicate()
        wall_s = round(time.monotonic() - t0, 3)
        cid = cidfile.read_text().strip() if cidfile.exists() else None
        gone = teardown(cid)

    try:
        evidence = json.loads(out)
    except ValueError:
        evidence = None
    proof = namespace_proof(evidence)
    receipt = {
        "container_id": cid,
        "container_name": name,
        "image": IMAGE,
        "docker_flags": flags[flags.index("--network"):],
        "started_at": started_at,
        "wall_s": wall_s,
        "exit_code": proc.returncode,
        "hard_killed": hard_killed,
        "chromium_sandbox": proof,
        "teardown": gone,
    }
    if evidence is None:
        receipt["stderr_tail"] = err.decode("utf-8", "replace")[-1000:]
    ok = evidence is not None and proof["in_new_user_pid_net_ns"] > 0 and gone["container_gone"]
    return {"ok": ok, "evidence": evidence, "receipt": receipt}


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


def summary(result):
    e, r = result["evidence"] or {}, result["receipt"]
    ns = r["chromium_sandbox"]
    lines = [f"final url    {e.get('final_url')}",
             f"timed out    {e.get('timed_out')}  errors {len(e.get('errors', []))}"]
    lines += [f"redirect     {x['type']}: {x['from']} -> {x['to']}" for x in e.get("redirects", [])]
    lines += [f"destination  {d['kind']}: {d['url']} (same host: {d['same_host_as_page']})"
              for d in e.get("destinations", [])]
    lines += [f"password     {len(e.get('password_fields', []))} field(s)",
              f"hidden text  {[h['text'] for h in e.get('hidden_text', [])]}",
              f"screenshots  {sum(1 for p in e.get('pages', []) if p.get('screenshot'))}",
              f"container    {(r['container_id'] or '')[:12]} exit {r['exit_code']} wall {r['wall_s']} s",
              f"namespaces   {ns['in_new_user_pid_net_ns']}/{ns['chromium_processes']} Chromium processes "
              f"in new user+pid+net ns {ns['isolated_types']}",
              f"teardown     container gone: {r['teardown']['container_gone']}",
              f"ok           {result['ok']}"]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--case", default="cli")
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    result = run_visit(a.url, a.case)
    save(result, Path(a.out) / a.case)
    print(summary(result))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
