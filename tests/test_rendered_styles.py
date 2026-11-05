"""Computed styles in a real browser: buttons people can read, and cut text that stops exactly at its last line."""
import json

import pytest

from tests.in_browser import run_in_frames
from tests.test_appeals import second, suspended  # noqa: F401
from tests.test_live_views import start, twins  # noqa: F401
from tests.test_model_pipeline import URL_A, URL_B, client, log, replies, run  # noqa: F401

READABLE = """
  const rgb = s => (s.match(/[\\d.]+/g) || []).map(Number);
  const opaque = c => c.length < 4 || c[3] > 0;
  const lum = c => { const f = c.slice(0, 3).map(v => { v /= 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; });
                     return .2126 * f[0] + .7152 * f[1] + .0722 * f[2]; };
  const backdrop = e => { for (; e; e = e.parentElement) { const c = rgb(win.getComputedStyle(e).backgroundColor);
                          if (c.length && opaque(c)) return c; } return [255, 255, 255]; };
  return [...doc.querySelectorAll('a.btn, button')].map(e => {
    const text = rgb(win.getComputedStyle(e).color), back = backdrop(e);
    const [hi, lo] = [lum(text), lum(back)].sort((a, b) => b - a);
    return {name: e.innerText.trim().slice(0, 30), ratio: Math.round((hi + .05) / (lo + .05) * 100) / 100};
  });
"""
CUT_TEXT = """
  return [...doc.querySelectorAll('.clamp')].map(e => {
    const style = win.getComputedStyle(e);
    const box = e.parentElement.getBoundingClientRect(), text = e.getBoundingClientRect();
    return {height: text.height, line: parseFloat(style.lineHeight), hidden: e.scrollHeight > e.clientHeight + 1,
            padding: parseFloat(style.paddingBottom), bottomGap: box.bottom - text.bottom,
            boxPadding: parseFloat(win.getComputedStyle(e.parentElement).paddingBottom)};
  });
"""


def assert_readable(pages, tmp_path):
    results = run_in_frames(pages, READABLE, tmp_path)
    assert any(results.values())
    for frame, buttons in results.items():
        for b in buttons:
            assert b["ratio"] >= 4.5, f"{frame}: {b}"


def test_buttons_are_readable_on_the_empty_front_page_and_a_case(client, tmp_path):  # noqa: F811
    c = run(client, URL_A)
    empty_front = client.get("/").text
    assert "Run the comparison" in empty_front
    assert_readable({"front": empty_front, "case": client.get(f"/cases/{c['id']}").text}, tmp_path)


def test_buttons_are_readable_on_a_decided_case_and_for_the_customer(client, second, tmp_path):  # noqa: F811
    c = suspended(client)
    assert_readable({"decided": client.get(f"/cases/{c['id']}").text,
                     "customer": client.get(f"/appeal/{c['appeal_token']}").text}, tmp_path)


def test_buttons_are_readable_with_a_finished_comparison(twins, tmp_path):  # noqa: F811
    result = start(twins)
    assert_readable({"front": twins.get("/").text, "compare": twins.get("/compare").text,
                     "result": twins.get(result).text}, tmp_path)


@pytest.fixture
def long_claims(client, replies):  # noqa: F811
    replies["vision_claim"] = json.dumps({"description": "A long claim about the page, written at length. " * 12,
                                          "imitated_brand": None})
    case_id = client.post("/api/reports", json={"url": URL_B, "claim": "A long report with many words in it. " * 30}).json()["case_id"]
    return client.get(f"/cases/{case_id}").text


def test_cut_text_stops_at_its_fifth_line(long_claims, tmp_path):
    results = run_in_frames({"case": long_claims}, CUT_TEXT, tmp_path)
    for frame, blocks in results.items():
        assert len(blocks) == 2, frame
        for b in blocks:
            assert abs(b["height"] - 5 * b["line"]) <= 1, f"{frame}: {b}"
            assert b["hidden"] and b["padding"] == 0, f"{frame}: {b}"
            assert abs(b["bottomGap"] - b["boxPadding"]) <= 1, f"{frame}: {b}"


def test_show_all_still_holds_the_whole_text(long_claims):
    whole = long_claims[long_claims.index('id="claims-all"'):]
    assert ("A long report with many words in it. " * 30).strip() in whole
    assert ("A long claim about the page, written at length. " * 12).strip() in whole
