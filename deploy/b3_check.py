"""Run on the VM, as root, from the app folder: does Chromium's own sandbox work under the contract flags?

Launches Chromium once with the exact flags of sandbox/launch.py. Only the network differs: a throwaway internal one,
because this runs before the stack exists. Exit 0 means mode B3 works here.
"""
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sandbox import launch  # noqa: E402

NETWORK = "dp-b3-check"


def main():
    subprocess.run(["docker", "network", "rm", NETWORK], capture_output=True)
    subprocess.run(["docker", "network", "create", "--internal", NETWORK], check=True, capture_output=True)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            args = launch.docker_args(f"dp-b3-{uuid.uuid4().hex[:8]}", Path(tmp) / "cid")
            args[args.index(launch.NETWORK)] = NETWORK
            r = subprocess.run(args + ["--launch-only", "--sandbox", "on"], capture_output=True, text=True, timeout=90)
    finally:
        subprocess.run(["docker", "network", "rm", NETWORK], capture_output=True)

    try:
        browser = json.loads(r.stdout)["browser"]
    except ValueError:
        print(f"no JSON, exit {r.returncode}\n{r.stderr[-1500:]}")
        return 1
    if "launch_error" in browser:
        lines = [x for x in browser["launch_error"].splitlines() if x.strip()]
        print("FAIL: Chromium did not launch with its sandbox on")
        print("\n".join(x[:200] for x in lines if "FATAL" in x or "sandbox" in x.lower() or "Check failed" in x)[:1500])
        return 1
    proof = browser["sandbox_evidence"]
    types = sorted({p["type"] for p in proof["processes"] if p["new_user_ns"] and p["new_pid_ns"] and p["new_net_ns"]})
    ok = proof["in_new_user_pid_net_ns"] > 0
    print(f"{'OK' if ok else 'FAIL'}: Chromium {browser['version']}, {proof['in_new_user_pid_net_ns']}/"
          f"{proof['chromium_processes']} processes in new user+pid+net namespaces {types}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
