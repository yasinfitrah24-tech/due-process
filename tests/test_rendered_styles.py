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


PILLS = """
  const rgb = s => (s.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
  const lum = c => { const f = c.map(v => { v /= 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; });
                     return .2126 * f[0] + .7152 * f[1] + .0722 * f[2]; };
  return [...doc.querySelectorAll('.pill, .badge')].map(e => {
    const s = win.getComputedStyle(e), text = rgb(s.color), back = rgb(s.backgroundColor);
    const [hi, lo] = [lum(text), lum(back)].sort((a, b) => b - a);
    return {words: e.innerText.trim(), classes: e.className, text, back, ratio: (hi + .05) / (lo + .05)};
  });
"""
HEX = {"Proven": ((0xA3, 0x22, 0x1A), (0xFB, 0xEA, 0xE8)), "Suspend": ((0xA3, 0x22, 0x1A), (0xFB, 0xEA, 0xE8)),
       "Suspicious": ((0x9A, 0x4A, 0x06), (0xFD, 0xEF, 0xE0)), "Warn": ((0x9A, 0x4A, 0x06), (0xFD, 0xEF, 0xE0)),
       "Not proven": ((0x3E, 0x4A, 0x5C), (0xE9, 0xED, 0xF3)), "Close": ((0x3E, 0x4A, 0x5C), (0xE9, 0xED, 0xF3))}
SOLID = {"Proven": (0xB3, 0x26, 0x1E), "Suspicious": (0xB4, 0x53, 0x09), "Not proven": (0x4B, 0x58, 0x68)}
QUEUE_ROWS = """<table class="queue"><tbody>""" + "".join(
    f'<tr><td><span class="pill pill-{w.replace(" ", "")}">{w}</span></td></tr>' for w in HEX) + """</tbody></table>"""
LEVELS = "".join(f'<p class="badge badge-{w.replace(" ", "")}">{w}</p>' for w in SOLID)


def test_every_level_and_decision_has_its_words_and_its_color(tmp_path):
    page = f"<!doctype html><html><head></head><body>{QUEUE_ROWS}{LEVELS}</body></html>"
    for frame, pills in run_in_frames({"pills": page}, PILLS, tmp_path).items():
        assert len(pills) == len(HEX) + len(SOLID)
        for p in pills:
            assert p["words"] and p["ratio"] >= 4.5, f"{frame}: {p}"
            if "badge" in p["classes"]:
                assert tuple(p["back"]) == SOLID[p["words"]] and p["text"] == [255, 255, 255], f"{frame}: {p}"
            else:
                assert (tuple(p["text"]), tuple(p["back"])) == HEX[p["words"]], f"{frame}: {p}"


def test_queue_and_case_use_the_level_pills(client):  # noqa: F811
    c = run(client, URL_A)
    assert 'class="pill pill-Proven">Proven<' in client.get("/partials/queue").text
    assert 'badge badge-Proven">Proven<' in client.get(f"/cases/{c['id']}").text


NAVIGATION = """
  const back = doc.querySelector('.back'), links = [...doc.querySelectorAll('.site-nav a')];
  return {back: back ? back.getBoundingClientRect().height : null, width: win.innerWidth,
          lines: links.map(a => win.getComputedStyle(a).textDecorationLine),
          heights: links.map(a => a.getBoundingClientRect().height),
          current: links.filter(a => a.getAttribute('aria-current') === 'page').length};
"""


def test_menu_has_no_underlines_and_marks_the_page_and_the_back_button_fits_a_finger(client, tmp_path):  # noqa: F811
    c = run(client, URL_A)
    results = run_in_frames({"front": client.get("/").text, "case": client.get(f"/cases/{c['id']}").text}, NAVIGATION, tmp_path)
    for frame, n in results.items():
        assert set(n["lines"]) == {"none"} and min(n["heights"]) >= 44, f"{frame}: {n}"
        assert n["current"] == 1, f"{frame}: {n}"
        if frame.startswith("case"):
            assert n["back"] >= (44 if n["width"] <= 375 else 40), f"{frame}: {n}"


QUEUE_SHOWN = """return [...doc.querySelectorAll('#queue tbody tr')].filter(r => r.getBoundingClientRect().height > 0).length;"""


def test_the_front_page_shows_twelve_rows_until_asked(client, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr("app.main.submit", lambda case_id: None)
    for _ in range(9):
        client.post("/api/reports", json={"url": URL_A, "claim": "c"})
    assert "Show all" not in client.get("/").text
    monkeypatch.setattr("app.ratelimit.allow", lambda *a, **k: True)
    for _ in range(6):
        client.post("/api/reports", json={"url": URL_A, "claim": "c"})
    page = client.get("/").text
    assert "Show all 15 cases" in page
    opened = page.replace('id="queue-all"', 'id="queue-all" open')
    results = run_in_frames({"folded": page, "open": opened}, QUEUE_SHOWN, tmp_path)
    for frame, rows in results.items():
        assert rows == (12 if frame.startswith("folded") else 15), f"{frame}: {rows}"


ACTIONS = """
  return [...doc.querySelectorAll('[class*=act-]')].map(e => {
    const s = win.getComputedStyle(e);
    return {value: e.value, color: s.color, back: s.backgroundColor, border: s.borderTopColor};
  });
"""
WHITE, CARD = "rgb(255, 255, 255)", "rgb(255, 255, 255)"
DECISIONS = {"Suspend": (WHITE, "rgb(179, 38, 30)", "rgb(179, 38, 30)"), "Warn": ("rgb(154, 74, 6)", CARD, "rgb(180, 83, 9)"),
             "Close": ("rgb(62, 74, 92)", CARD, "rgb(75, 88, 104)"), "Keep": ("rgb(62, 74, 92)", CARD, "rgb(75, 88, 104)"),
             "Reinstate": (WHITE, "rgb(31, 111, 74)", "rgb(31, 111, 74)")}


def assert_decision_colors(results, expected):
    for frame, buttons in results.items():
        assert {b["value"] for b in buttons} == expected, frame
        for b in buttons:
            assert (b["color"], b["back"], b["border"]) == DECISIONS[b["value"]], f"{frame}: {b}"


def test_action_buttons_have_the_colors_of_their_decision(client, tmp_path):  # noqa: F811
    c = run(client, URL_A)
    results = run_in_frames({"case": client.get(f"/cases/{c['id']}").text}, ACTIONS, tmp_path)
    assert_decision_colors(results, {"Suspend", "Warn", "Close"})


def test_appeal_buttons_have_the_colors_of_their_decision(client, second, tmp_path):  # noqa: F811
    c = suspended(client)
    client.post(f"/appeal/{c['appeal_token']}", data={"choice": "fixed"})
    results = run_in_frames({"case": client.get(f"/cases/{c['id']}").text}, ACTIONS, tmp_path)
    assert_decision_colors(results, {"Reinstate", "Keep"})
    assert_readable({"appeal": client.get(f"/cases/{c['id']}").text}, tmp_path)


def test_levels_are_pills_on_compare_and_for_the_customer(twins, tmp_path):  # noqa: F811
    page = twins.get(start(twins)).text
    assert 'class="badge badge-Proven">Proven<' in page and 'class="badge badge-Notproven">Not proven<' in page
    assert 'class="pill pill-Proven">Proven<' in page


def test_appeal_visits_show_levels_as_pills(client, second):  # noqa: F811
    c = suspended(client)
    client.post(f"/appeal/{c['appeal_token']}", data={"choice": "fixed"})
    page = client.get(f"/appeal/{c['appeal_token']}").text
    assert 'class="pill pill-Proven">Proven<' in page and 'pill-on-navy pill-Notproven">Not proven<' in page
    assert 'aria-current="page"' in page
