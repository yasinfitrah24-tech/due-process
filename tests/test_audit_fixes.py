"""The fixes from docs/ui-audit.md, in behavior: repeated forms, visits with no evidence, and what a reader is told."""
import copy
import re
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import db, main, pipeline
from tests.evidence import PAGE_A_EVIDENCE, visit_result
from tests.test_appeals import appeal, second, suspended  # noqa: F401
from tests.test_live_views import start, twins  # noqa: F401
from tests.test_model_pipeline import URL_A, client, log, replies, run  # noqa: F401

NONCE = {"nonce": "form-1"}


def count_visits(monkeypatch):
    visits = []
    real = pipeline.visitor.run_visit

    def visit(url, case, on_event=None):
        visits.append(url)
        return real(url, case, on_event)

    monkeypatch.setattr(pipeline.visitor, "run_visit", visit)
    return visits


def cases():
    with db.connect() as c:
        return c.execute("SELECT COUNT(*) FROM cases").fetchone()[0]


def model_calls():
    with db.connect() as c:
        return c.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0]


def test_two_quick_posts_are_one_sandbox_and_one_set_of_model_calls(client, monkeypatch):  # noqa: F811
    visits = count_visits(monkeypatch)
    first = client.post("/try/phishing", data=NONCE, follow_redirects=False)
    calls = model_calls()
    again = client.post("/try/phishing", data=NONCE, follow_redirects=False)
    assert first.status_code == again.status_code == 303
    assert again.headers["location"] == first.headers["location"]
    assert len(visits) == 1 and cases() == 1 and calls > 0 and model_calls() == calls


def test_two_posts_at_the_same_moment_are_one_sandbox(client, monkeypatch):  # noqa: F811
    visits = count_visits(monkeypatch)
    with ThreadPoolExecutor(2) as pool:
        answers = list(pool.map(lambda _: client.post("/try/phishing", data=NONCE, follow_redirects=False), range(2)))
    assert [a.status_code for a in answers] == [303, 303]
    assert answers[0].headers["location"] == answers[1].headers["location"]
    assert len(visits) == 1 and cases() == 1


def test_a_new_form_is_a_new_request(client, monkeypatch):  # noqa: F811
    visits = count_visits(monkeypatch)
    client.post("/try/phishing", data={"nonce": "a"})
    client.post("/try/phishing", data={"nonce": "b"})
    assert len(visits) == 2


@pytest.mark.parametrize("path, data", [("/reports", {"url": URL_A, "claim": "c"}), ("/test-url", {"url": URL_A})])
def test_report_forms_are_spent_once(client, monkeypatch, path, data):  # noqa: F811
    visits = count_visits(monkeypatch)
    answers = [client.post(path, data={**data, **NONCE}, follow_redirects=False) for _ in range(2)]
    assert len(visits) == 1 and answers[0].headers["location"] == answers[1].headers["location"]


def test_a_rejected_form_can_be_sent_again(client):  # noqa: F811
    data = {"url": "http://unknown.test/", "claim": "c", **NONCE}
    assert client.post("/reports", data=data).status_code == 422
    assert client.post("/reports", data=data).status_code == 422
    assert client.post("/reports", data={"url": URL_A, "claim": "c", **NONCE}, follow_redirects=False).status_code == 303


def test_a_repeated_comparison_starts_nothing(twins, monkeypatch):  # noqa: F811
    visits = count_visits(monkeypatch)
    answers = [twins.post("/compare/run/login", data=NONCE, follow_redirects=False) for _ in range(2)]
    assert len(visits) == 2 and answers[0].headers["location"] == answers[1].headers["location"]


def test_a_repeated_action_takes_one_action_and_no_error(client):  # noqa: F811
    c = run(client, URL_A)
    data = {"action": "Warn", **NONCE}
    answers = [client.post(f"/cases/{c['id']}/actions", data=data, follow_redirects=False) for _ in range(2)]
    assert [a.status_code for a in answers] == [303, 303]
    audit = client.get(f"/api/cases/{c['id']}").json()["audit"]
    assert len([a for a in audit if a["event"] == "action_taken"]) == 1


def test_a_repeated_letter_is_sent_once(client):  # noqa: F811
    c = run(client, URL_A)
    client.post(f"/api/cases/{c['id']}/actions", json={"action": "Warn"})
    for _ in range(2):
        client.post(f"/cases/{c['id']}/documents/reporter_reply/send", data=NONCE)
    audit = client.get(f"/api/cases/{c['id']}").json()["audit"]
    assert len([a for a in audit if a["event"] == "document_sent"]) == 1


def test_a_repeated_appeal_is_one_second_visit(client, second):  # noqa: F811
    c = suspended(client)
    for _ in range(2):
        client.post(f"/appeal/{c['appeal_token']}", data={"choice": "fixed", **NONCE})
    assert len(second["calls"]) == 1


def test_every_form_that_starts_work_carries_a_nonce(client):  # noqa: F811
    c = run(client, URL_A)
    for page in (client.get("/").text, client.get("/compare").text, client.get(f"/cases/{c['id']}").text):
        for form in re.findall(r"<form.*?</form>", page, re.S):
            assert 'name="nonce"' in form


def failed_visit(client, monkeypatch, gone=True):  # noqa: F811
    def visit(url, case, on_event=None):
        result = visit_result(copy.deepcopy(PAGE_A_EVIDENCE), ok=False)
        result["receipt"]["teardown"]["container_gone"] = gone
        return result

    monkeypatch.setattr(pipeline.visitor, "run_visit", visit)
    c = run(client, URL_A)
    return c, client.get(f"/cases/{c['id']}").text


def test_a_failed_visit_shows_no_evidence_block(client, monkeypatch):  # noqa: F811
    c, page = failed_visit(client, monkeypatch)
    assert 'class="evidence solid"' not in page and 'class="evidence pending"' in page
    assert "<h2>Nothing was reproduced</h2>" in page and "<h2>Evidence the browser reproduced</h2>" not in page
    assert "The sandbox visit did not complete" in page and c["level"] == "Not proven" and "badge-Notproven" in page


def test_a_reproduced_case_keeps_the_evidence_block(client):  # noqa: F811
    c = run(client, URL_A)
    assert 'class="evidence solid"' in client.get(f"/cases/{c['id']}").text


def test_a_failed_second_visit_shows_no_evidence_block(client, second):  # noqa: F811
    second["ok"] = False
    c = suspended(client)
    appeal(client, c)
    for page in (client.get(f"/cases/{c['id']}").text, client.get(f"/appeal/{c['appeal_token']}").text):
        visits = page[page.index('class="visits'):]
        assert 'class="solid"' not in visits[:900] and "No result" in visits and "Nothing was reproduced" in visits


def test_a_container_left_behind_is_shown_beside_the_level(client, monkeypatch):  # noqa: F811
    _, page = failed_visit(client, monkeypatch, gone=False)
    assert page.index("badge-Notproven") < page.index("Container still present") < page.index("Technical evidence")
    assert "An operator must remove it." in page


def test_no_warning_when_the_container_is_gone(client, monkeypatch):  # noqa: F811
    _, page = failed_visit(client, monkeypatch)
    assert "Container still present" not in page


def test_a_rejected_report_keeps_what_was_typed_and_takes_focus(client):  # noqa: F811
    r = client.post("/reports", data={"url": "http://unknown.test/x", "claim": "My <b>words</b>"})
    form = r.text[r.text.index('id="report"'):r.text.index('id="try-appeal"')]
    assert "data-focus" in form and "Rejected: not our customer" in form
    assert 'value="http://unknown.test/x"' in form and "My &lt;b&gt;words&lt;/b&gt;" in form
    assert "data-fold" not in form[:80]


def test_a_rejected_test_url_is_answered_at_its_own_form(client):  # noqa: F811
    r = client.post("/test-url", data={"url": "ftp://x.test/"})
    box = r.text[r.text.index('id="test-any"'):r.text.index('id="measured"')]
    assert "Rejected:" in box and 'value="ftp://x.test/"' in box
    assert "Rejected:" not in r.text[r.text.index('id="report"'):r.text.index('id="test-any"')]


def test_the_level_is_in_what_is_announced(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(main, "submit", lambda case_id: None)
    case_id = client.post("/api/reports", json={"url": URL_A, "claim": "c"}).json()["case_id"]
    assert 'data-status="Received. Waiting for a sandbox."' in client.get(f"/cases/{case_id}").text
    db.update_case(case_id, status="drafting", level="Proven")
    assert 'data-status="Level: Proven. Received, reproduced.' in client.get(f"/partials/cases/{case_id}").text


def test_compare_announces_both_levels(twins):  # noqa: F811
    page = twins.get(start(twins)).text
    status = re.search(r'id="compare" data-status="([^"]*)"', page)[1]
    assert ": Proven." in status and ": Not proven." in status


def test_the_header_marks_where_you_are(client):  # noqa: F811
    assert client.get("/").text.count('aria-current="page"') == 1
    compare = client.get("/compare").text
    assert re.search(r'<a href="/compare" aria-current="page"', compare)
    assert 'href="/#queue" aria-current' not in compare


def test_an_empty_queue_says_what_to_do(client):  # noqa: F811
    page = client.get("/").text
    assert "No reports yet." in page and 'href="#try"' in page and "solid reveal" not in page
