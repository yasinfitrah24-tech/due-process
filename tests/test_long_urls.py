"""A URL of 2000 characters is cut wherever it is shown, and stays whole in title and in the case's record."""
import re

import pytest

from app import explain, main
from tests.test_model_pipeline import URL_A, client, log, replies  # noqa: F401

LONG = URL_A + "/" + "a-very-long-path/" * 115
LONG = LONG[:2000]


@pytest.fixture
def case(client, monkeypatch):  # noqa: F811
    real = main.pipeline.visitor.run_visit
    monkeypatch.setattr(main.pipeline.visitor, "run_visit", lambda url, case, on_event=None: real(URL_A, case, on_event))
    return client.post("/api/reports", json={"url": LONG, "claim": "c"}).json()["case_id"]


def shown_uncut(page):
    """Places where the whole URL is text a reader sees outside a cut line."""
    cut = re.sub(r'<(\w+)[^>]*class="[^"]*url-line[^"]*"[^>]*>.*?</\1>', "", page, flags=re.S)
    cut = re.sub(r'title="[^"]*"', "", cut)
    return cut.count(LONG)


def test_the_queue_cuts_a_long_url_and_keeps_it_in_the_title(client, case):  # noqa: F811
    rows = client.get("/partials/queue").text
    assert 'class="code url-line"' in rows and f'title="{LONG}"' in rows
    assert shown_uncut(rows) == 0


def test_the_case_head_cuts_it_and_the_record_has_it_whole(client, case):  # noqa: F811
    page = client.get(f"/cases/{case}").text
    head = page[:page.index('class="case-grid"')]
    assert f'title="{LONG}"' in head and shown_uncut(head) == 0
    technical = page[page.index('id="technical"'):]
    assert f'Reported URL: <span class="code">{LONG}</span>' in technical


def test_a_sentence_cuts_a_long_url_for_display_only():
    text = f"The page at {LONG} asks for a password."
    assert explain.short_urls(text) == f"The page at {LONG[:70]}… asks for a password."


def test_a_normal_url_is_left_alone():
    text = "The page at http://tailspin-offers.test/start redirected."
    assert explain.short_urls(text) == text


def test_the_timeline_shows_a_cut_url(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(main, "submit", lambda case_id: None)
    case_id = client.post("/api/reports", json={"url": LONG, "claim": "c"}).json()["case_id"]
    main.db.add_event(case_id, None, {"source": "browser", "kind": "navigation", "url": LONG})
    page = client.get(f"/cases/{case_id}").text
    progress = page[page.index("Live progress"):page.index('id="technical"')]
    assert f"Browser opened {LONG[:70]}…" in progress and shown_uncut(progress) == 0
