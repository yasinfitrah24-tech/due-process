"""Step 5 internet check, run inside the runner (same limits): every outside target must fail, testsite must work.

usage: docker run -i <limits> --entrypoint python3 dp-runner - < netprobe.py
"""
import json
import socket

# (label, host, port, expect_ok)
TARGETS = [
    ("control: testsite", "testsite", 80, True),
    ("dns example.com", "example.com", 80, False),
    ("tcp 1.1.1.1:443", "1.1.1.1", 443, False),
    ("tcp 8.8.8.8:53", "8.8.8.8", 53, False),
    ("docker desktop host", "host.docker.internal", 80, False),
    ("docker desktop proxy", "http.docker.internal", 3128, False),
]

out = []
for label, host, port, expect_ok in TARGETS:
    r = {"target": label, "host": host, "port": port, "expect_ok": expect_ok}
    try:
        with socket.create_connection((host, port), timeout=5):
            r["result"] = "connected"
    except OSError as e:
        r["result"] = f"{type(e).__name__}: {e}"
    r["pass"] = (r["result"] == "connected") == expect_ok
    out.append(r)
print(json.dumps(out))
