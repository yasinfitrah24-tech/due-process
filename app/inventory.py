"""Simulated hosting inventory. The action target comes from here, by URL host, never from report text."""
import json
from pathlib import Path
from urllib.parse import urlsplit

CUSTOMERS = json.loads((Path(__file__).parent / "inventory.json").read_text())["customers"]
BY_HOST = {h: c for c in CUSTOMERS for h in c["hosts"]}


class BadUrl(ValueError):
    pass


def reported_host(url):
    """The host of a reported URL. Refuses URLs that Python and Chromium could read as different hosts:
    a backslash ends the host for Chromium but not for urlsplit, and tabs and newlines are stripped."""
    if any(ch == "\\" or ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in url):
        raise BadUrl("URL has characters that are not allowed")
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        parts.port
    except ValueError:
        raise BadUrl("URL could not be parsed") from None
    if parts.scheme not in ("http", "https") or not hostname:
        raise BadUrl("only http and https URLs can be reported")
    if "@" in parts.netloc:
        raise BadUrl("URLs with a user name are not allowed")
    return hostname


def customer_for(host):
    return BY_HOST.get(host)
