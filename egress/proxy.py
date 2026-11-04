"""Egress proxy: the only way out of the sandbox network. Logs every request as one JSON line on stdout.

Checks the name and every address it resolves to, then connects to the address it checked, so a name cannot resolve
to something else between the check and the connection. Only addresses on the public internet are allowed, plus the
exact test hostnames in EGRESS_ALLOW_PRIVATE_HOSTS.
"""
import asyncio
import ipaddress
import json
import os
import socket
import time
from urllib.parse import urlsplit

METADATA_IP = ipaddress.ip_address("169.254.169.254")
ALLOWED_PORTS = {80, 443}
DENIED_NAMES = {"app", "launcher", "egress", "host.docker.internal", "gateway.docker.internal", "metadata",
                "metadata.google.internal"}
HEAD_LIMIT = 65536
HEAD_TIMEOUT_S = 15
CONNECT_TIMEOUT_S = 15
IDLE_TIMEOUT_S = 70
DROPPED_HEADERS = {"proxy-connection", "proxy-authorization", "connection", "keep-alive"}


def names(variable):
    return {n.strip().lower() for n in os.environ.get(variable, "").split(",") if n.strip()}


def resolve(host, port):
    return sorted({info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})


def address_problem(text, denied_ips):
    ip = ipaddress.ip_address(text.split("%")[0])
    ip = getattr(ip, "ipv4_mapped", None) or ip
    if ip == METADATA_IP:
        return "cloud metadata address"
    if str(ip) in denied_ips:
        return "denied address"
    if not ip.is_global:
        return "private or local address"
    return None


def decide(host, port, allow_private, denied_ips, resolver=resolve):
    """Returns (address to connect to, None) or (None, reason)."""
    host = host.lower().rstrip(".")
    if host in DENIED_NAMES:
        return None, "denied host"
    if port not in ALLOWED_PORTS:
        return None, "port not allowed"
    try:
        addresses = resolver(host, port)
    except OSError:
        return None, "name did not resolve"
    if not addresses:
        return None, "name did not resolve"
    if host in allow_private:
        return addresses[0], None
    for address in addresses:
        problem = address_problem(address, denied_ips)
        if problem:
            return None, problem
    return addresses[0], None


def log(**entry):
    print(json.dumps({"ts": round(time.time(), 3), **entry}), flush=True)


def target_of(method, target):
    """(scheme, host, port, origin-form path) of a proxy request line."""
    if method == "CONNECT":
        parts = urlsplit(f"//{target}")
        return "https", parts.hostname, parts.port or 443, None
    parts = urlsplit(target)
    if parts.scheme != "http" or not parts.hostname:
        raise ValueError("only absolute http URLs")
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return "http", parts.hostname, parts.port or 80, path


def forwarded_head(method, path, lines):
    kept = [line for line in lines if line.split(":", 1)[0].strip().lower() not in DROPPED_HEADERS]
    return "\r\n".join([f"{method} {path} HTTP/1.1", *kept, "Connection: close", "", ""]).encode("latin-1")


async def relay(reader, writer):
    try:
        while data := await asyncio.wait_for(reader.read(65536), IDLE_TIMEOUT_S):
            writer.write(data)
            await writer.drain()
    except (OSError, asyncio.TimeoutError):
        pass
    finally:
        writer.close()


def reply(writer, status, reason):
    body = f"Blocked by egress proxy: {reason}\n".encode()
    writer.write(f"HTTP/1.1 {status} Blocked\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\n"
                 "Connection: close\r\n\r\n".encode() + body)


async def handle(reader, writer, allow_private, denied_ips):
    client = (writer.get_extra_info("peername") or ("?",))[0]
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT_S)
        request, *lines = head[:-4].decode("latin-1").split("\r\n")
        method, target, _ = request.split(" ", 2)
        scheme, host, port, path = target_of(method, target)
    except asyncio.IncompleteReadError as e:
        if e.partial:  # a connection that closes without a byte is not a request
            log(client=client, host=None, decision="blocked", reason="bad request")
        writer.close()
        return
    except (ValueError, OSError, asyncio.TimeoutError, asyncio.LimitOverrunError):
        log(client=client, host=None, decision="blocked", reason="bad request")
        writer.close()
        return

    # The query can hold what a page tries to send out, so only its absence or presence is kept.
    logged_path = path.split("?")[0] if path else None
    entry = {"client": client, "method": method, "scheme": scheme, "host": host, "port": port, "path": logged_path}
    loop = asyncio.get_running_loop()
    address, reason = await loop.run_in_executor(None, decide, host, port, allow_private, denied_ips)
    if address is None:
        log(**entry, decision="blocked", reason=reason)
        reply(writer, 403, reason)
        await writer.drain()
        writer.close()
        return
    try:
        up_reader, up_writer = await asyncio.wait_for(asyncio.open_connection(address, port), CONNECT_TIMEOUT_S)
    except (OSError, asyncio.TimeoutError):
        log(**entry, decision="failed", reason="could not connect", address=address)
        reply(writer, 502, "could not connect")
        await writer.drain()
        writer.close()
        return

    log(**entry, decision="allowed", address=address)
    if method == "CONNECT":
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
    else:
        up_writer.write(forwarded_head(method, path, lines))
    await asyncio.gather(relay(reader, up_writer), relay(up_reader, writer))


async def main():
    allow_private, denied_ips = names("EGRESS_ALLOW_PRIVATE_HOSTS"), names("EGRESS_DENY_IPS")
    server = await asyncio.start_server(lambda r, w: handle(r, w, allow_private, denied_ips), "0.0.0.0", 3128,
                                        limit=HEAD_LIMIT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
