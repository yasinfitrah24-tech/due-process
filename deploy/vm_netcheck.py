"""Run on the VM from the app folder: the network test, inside a real sandbox with the contract flags.

usage: python3 deploy/vm_netcheck.py PUBLIC_IP PUBLIC_HOST
"""
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sandbox import launch  # noqa: E402


def main():
    ip, host = sys.argv[1:3]
    with tempfile.TemporaryDirectory() as tmp:
        args = launch.docker_args(f"dp-case-net-{uuid.uuid4().hex[:8]}", Path(tmp) / "cid")
        args[-1:-1] = ["-i", "--entrypoint", "python3"]
        script = (ROOT / "tests" / "sandbox_netcheck.py").read_text()
        r = subprocess.run(args + ["-", ip, host], input=script, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print(r.stderr[-1500:])
        return 1
    result = json.loads(r.stdout)
    direct = {k: v for k, v in result["direct"].items() if k != "egress:3128"}
    proxied = {k: v for k, v in result["proxy"].items() if k != "demo_host"}
    print(json.dumps(result, indent=1))
    ok = (result["direct"]["egress:3128"] == "connected" and "connected" not in direct.values()
          and set(proxied.values()) == {403} and result["proxy"]["demo_host"] == 302)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
