"""Runs a script over rendered pages in a real headless browser, for what only computed styles can show.

Each page goes in a frame of the given width, with the stylesheet inlined and scripts removed, so sections stay open.
Fonts do not load here, which changes no color and no line height in pixels. Skips where no browser is installed.
"""
import html
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

CSS = (Path(__file__).parent.parent / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")
WINDOWS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
           r"C:\Program Files\Google\Chrome\Application\chrome.exe"]
OUTPUT = re.compile(r'<pre id="out">(.*?)</pre>', re.S)
WIDTHS = (375, 1280)


def find_browser():
    names = ("google-chrome", "chromium", "chromium-browser", "chrome", "msedge")
    found = next((p for p in map(shutil.which, names) if p), None)
    return found or next((p for p in WINDOWS if os.path.exists(p)), None)


def prepared(page):
    page = re.sub(r"<script\b.*?</script>", "", page, flags=re.S)
    page = re.sub(r"<link\b[^>]*>", "", page)
    return page.replace("</head>", f"<style>{CSS}</style></head>")


def run_in_frames(pages, script, tmp_path):
    """pages: {name: html}. script: a JS function body with `doc` and `win` that returns JSON. Returns {name@width: value}."""
    browser = find_browser()
    if browser is None:
        pytest.skip("no Chrome or Edge on this machine")
    frames = "".join(f'<iframe id="{name}@{width}" width="{width}" height="812" srcdoc="{html.escape(prepared(page))}"></iframe>'
                     for name, page in pages.items() for width in WIDTHS)
    harness = tmp_path / "harness.html"
    harness.write_text(f"""<!doctype html><meta charset="utf-8">{frames}<pre id="out"></pre><script>
      window.addEventListener('load', () => {{
        const check = (doc, win) => {{ {script} }};
        const out = {{}};
        document.querySelectorAll('iframe').forEach(f => {{ out[f.id] = check(f.contentDocument, f.contentWindow); }});
        document.getElementById('out').textContent = JSON.stringify(out);
      }});
    </script>""", encoding="utf-8")
    done = subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-sandbox", f"--user-data-dir={tmp_path / 'profile'}",
                           "--virtual-time-budget=5000", "--dump-dom", harness.as_uri()],
                          capture_output=True, text=True, encoding="utf-8", timeout=90)
    match = OUTPUT.search(done.stdout)
    assert match and match[1], f"no result from the browser: {done.stderr[-300:]}"
    return json.loads(html.unescape(match[1]))
