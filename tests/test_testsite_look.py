"""The look of the test pages is only a wrapper: banner, page content and the pages' behaviour stay as they were."""
import hashlib
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
BEFORE = json.loads((ROOT / "tests" / "testsite_bodies.json").read_text(encoding="utf-8"))
OPEN, CLOSE = '<div class="tp-card" id="page-body">', "</div></main>"
# Every address a test page may name. The test network has no other hosts, and the metadata address is the blocked target.
INTERNAL = {"collector.test", "northwind-verify.test", "birch-site.test", "auth.birch-site.test", "auth.northwind-bank.test",
            "harborlane.sites-demo.test", "login.fabrikam-id.test", "169.254.169.254"}


def load():
    spec = importlib.util.spec_from_file_location("testsite_server", ROOT / "testsite" / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SITE = load()
PAGES = {**{f"{host} {path}": body.decode() for (host, path), body in SITE.ROUTES.items()},
         "clean shop": SITE.CLEAN_SHOP.decode()}


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def split(html):
    start, end = html.index(OPEN) + len(OPEN), html.rindex(CLOSE)
    return html[:start], html[start:end], html[end:]


def test_every_page_starts_with_the_same_banner():
    for name, html in PAGES.items():
        assert html[html.index("<body>") + 6:].startswith(SITE.BANNER), name
        assert "<title>TEST PAGE - " in html, name
    assert "TEST PAGE &mdash; Northwind Bank is a fictional brand. Do not enter real data." in SITE.BANNER


def test_page_content_and_head_scripts_did_not_change():
    assert set(PAGES) == set(BEFORE)
    for name, html in PAGES.items():
        assert sha(split(html)[1]) == BEFORE[name]["body"], name
        assert sha(html[html.index("</style>") + 8:html.index("</head>")]) == BEFORE[name]["head"], name


def test_the_wrapper_asks_for_nothing():
    for name, html in PAGES.items():
        before, _, after = split(html)
        wrapper = re.sub(r"<script>.*?</script>", "", before + after, flags=re.S)
        assert not re.search(r"https?:|//|@import|url\(|\bsrc=|\bhref=|<link|<img", wrapper), name


def test_pages_name_only_internal_hosts():
    for name, html in PAGES.items():
        hosts = set(re.findall(r"https?://([^/'\"\s:]+)", html))
        assert hosts <= INTERNAL, (name, hosts - INTERNAL)
        assert "<form" not in split(html)[0] and "<form" not in split(html)[2], name


def test_twin_pages_look_the_same():
    for a, b in (("orchard-site.test /p/1042", "birch-site.test /p/2291"), ("birch-site.test /p/3310", "birch-site.test /p/6034"),
                 ("birch-site.test /p/7145", "orchard-site.test /p/8262"), ("birch-site.test /p/1173", "orchard-site.test /p/3481")):
        assert split(PAGES[a])[0] == split(PAGES[b])[0] and split(PAGES[a])[2] == split(PAGES[b])[2]


def test_the_only_new_text_is_the_brand_name():
    names = {"northwind-bank.test /login": "Northwind Bank", "tailspin-offers.test /sso-login": "Tailspin Offers",
             "clean shop": "Harbor Lane Books", "birch-site.test /p/1173": "Sample Site"}
    for key, name in names.items():
        before, _, after = split(PAGES[key])
        shown = re.sub(r"<[^>]+>", " ", re.sub(r"<(style|script|title|svg)\b.*?</\1>", " ", before + after, flags=re.S))
        title = re.search(r"<h1>(.*?)</h1>", before).group(1)
        banner = re.sub(r"<[^>]+>", "", SITE.BANNER)
        assert shown.split() == (banner + " " + name + " " + title).split(), key
    assert "#0B5D57" in PAGES["northwind-bank.test /login"] and "<footer" not in PAGES["northwind-bank.test /login"]
