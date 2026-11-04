"""Simulated hosting inventory. The action target comes from here, by URL host, never from report text."""
import ipaddress
import json
from pathlib import Path
from urllib.parse import urlsplit

from publicsuffixlist import PublicSuffixList

CUSTOMERS = json.loads((Path(__file__).parent / "inventory.json").read_text())["customers"]
BY_HOST = {h: c for c in CUSTOMERS for h in c["hosts"]}

# Keeps the list's private section, so two sites on a shared suffix such as github.io stay two parties.
SUFFIXES = PublicSuffixList()


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


def registrable_domain(host):
    """eTLD+1, or the host itself where there is none (IP addresses, single labels, public suffixes)."""
    if not host:
        return None
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        return SUFFIXES.privatesuffix(host) or host


def is_foreign(page_host, destination_host):
    """Foreign unless both hosts share a registrable domain or belong to the same customer."""
    if destination_host is None:
        return True
    if registrable_domain(page_host) == registrable_domain(destination_host):
        return False
    owner = customer_for(page_host)
    return owner is None or owner is not customer_for(destination_host)
