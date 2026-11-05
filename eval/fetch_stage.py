"""Fetch without a browser: runs on the sandbox network, through the egress proxy. run_eval.py prepends URLS.

One HTTP GET, following HTTP redirects only. No JavaScript, so no script redirects and no script handlers.
"""
import json
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urljoin

opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": "http://egress:3128", "https": "http://egress:3128"}))


class Forms(HTMLParser):
    def __init__(self):
        super().__init__()
        self.actions, self.passwords = [], 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and a.get("action") is not None:
            self.actions.append(a["action"])
        if tag == "input" and (a.get("type") or "").lower() == "password":
            self.passwords += 1


def fetch(url):
    t0 = time.monotonic()
    try:
        with opener.open(url, timeout=20) as r:
            final, body = r.geturl(), r.read(2_000_000).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        return {"url": url, "error": type(e).__name__, "wall_s": round(time.monotonic() - t0, 2)}
    forms = Forms()
    forms.feed(body)
    return {"url": url, "final_url": final, "actions": [urljoin(final, a) for a in forms.actions],
            "passwords": forms.passwords, "wall_s": round(time.monotonic() - t0, 2)}


print(json.dumps([fetch(u) for u in URLS]))  # noqa: F821
