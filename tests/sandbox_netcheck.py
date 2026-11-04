"""Runs inside a sandbox container. Prints one JSON object: what is reachable, directly and through the proxy."""
import json
import socket

PROXY = ("egress", 3128)


def direct(host, port):
    try:
        with socket.create_connection((host, port), timeout=3):
            return "connected"
    except OSError as e:
        return type(e).__name__


def through_proxy(request):
    """The status code the proxy answers with."""
    with socket.create_connection(PROXY, timeout=8) as s:
        s.sendall(request.encode())
        s.settimeout(8)
        return int(s.recv(4096).split(b" ", 2)[1])


def get(url, host):
    return through_proxy(f"GET {url} HTTP/1.1\r\nHost: {host}\r\n\r\n")


print(json.dumps({
    "direct": {f"{h}:{p}": direct(h, p) for h, p in [
        ("egress", 3128), ("tailspin-offers.test", 80), ("collector.test", 80), ("launcher", 8100), ("app", 8000),
        ("1.1.1.1", 443), ("8.8.8.8", 53), ("169.254.169.254", 80), ("host.docker.internal", 80)]},
    "proxy": {
        "metadata": get("http://169.254.169.254/latest/meta-data/", "169.254.169.254"),
        "metadata_decimal": get("http://2852039166/latest/meta-data/", "2852039166"),
        "metadata_connect": through_proxy("CONNECT 169.254.169.254:443 HTTP/1.1\r\nHost: x\r\n\r\n"),
        "private_10": get("http://10.0.0.1/", "10.0.0.1"),
        "private_192": get("http://192.168.65.1/", "192.168.65.1"),
        "loopback": get("http://127.0.0.1/", "127.0.0.1"),
        "launcher": get("http://launcher:8100/health", "launcher"),
        "launcher_port80": get("http://launcher/", "launcher"),
        "app": get("http://app:8000/", "app"),
        "docker_host": get("http://host.docker.internal/", "host.docker.internal"),
        "demo_host": get("http://tailspin-offers.test/start", "tailspin-offers.test"),
    },
}))
