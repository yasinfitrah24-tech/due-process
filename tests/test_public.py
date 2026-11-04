"""Public modes and limits."""
import json

from app import db, ratelimit, signals
from tests.evidence import PAGE_A_EVIDENCE
from tests.test_model_pipeline import URL_A, client, log, replies, run  # noqa: F401

INTERNET = "http://example.com/login"


def submit_url(client):  # noqa: F811
    r = client.post("/api/test-url", json={"url": INTERNET})
    assert r.status_code == 202, r.text
    return client.get(f"/api/cases/{r.json()['case_id']}").json()


def test_any_url_needs_no_inventory_and_has_no_target(client, monkeypatch):  # noqa: F811
    from app import pipeline, visitor
    from tests.evidence import visit_result
    monkeypatch.setattr(pipeline.visitor, "run_visit", lambda url, case: visit_result(json.loads(json.dumps(PAGE_A_EVIDENCE))))
    assert visitor is pipeline.visitor
    c = submit_url(client)
    assert c["mode"] == "test" and c["customer_id"] == "" and c["allowed_actions"] == []
    assert c["level"] == signals.PROVEN
    for action in ("Suspend", "Warn", "Close"):
        assert client.post(f"/api/cases/{c['id']}/actions", json={"action": action}).status_code == 403
    assert db.get_case(c["id"])["decided_action"] is None


def test_any_url_runs_no_claim_extraction_or_documents(client, monkeypatch, log):  # noqa: F811
    from app import pipeline
    from tests.evidence import visit_result
    monkeypatch.setattr(pipeline.visitor, "run_visit", lambda url, case: visit_result(json.loads(json.dumps(PAGE_A_EVIDENCE))))
    submit_url(client)
    assert [e["role"] for e in log] == ["verdict_claim"]


def test_any_url_page_says_there_is_no_target(client, monkeypatch):  # noqa: F811
    from app import pipeline
    from tests.evidence import visit_result
    monkeypatch.setattr(pipeline.visitor, "run_visit", lambda url, case: visit_result(json.loads(json.dumps(PAGE_A_EVIDENCE))))
    page = client.get(f"/cases/{submit_url(client)['id']}").text
    assert "no target and no action" in page and "not a finding of abuse" in page and "<h2>Decision</h2>" not in page


def test_any_url_still_refuses_misleading_urls(client):  # noqa: F811
    for url in ("file:///etc/passwd", "http://user@example.com/", "http://a.test\\@b.test/"):
        assert client.post("/api/test-url", json={"url": url}).status_code == 422


def test_demo_customers_still_work_without_login(client):  # noqa: F811
    c = run(client, URL_A)
    assert client.post(f"/api/cases/{c['id']}/actions", json={"action": "Suspend"}).status_code == 200


def test_rate_limit_per_ip(client):  # noqa: F811
    for _ in range(ratelimit.REPORTS_PER_WINDOW):
        assert client.post("/api/reports", json={"url": "http://example.com/", "claim": "c"}).status_code == 422
    assert client.post("/api/reports", json={"url": URL_A, "claim": "c"}).status_code == 429
    assert client.post("/api/test-url", json={"url": INTERNET}).status_code == 429
    assert client.post("/reports", data={"url": URL_A, "claim": "c"}).status_code == 429


def test_rate_limit_is_per_address_and_window():
    for _ in range(ratelimit.REPORTS_PER_WINDOW):
        assert ratelimit.allow("1.1.1.1", now=0)
    assert not ratelimit.allow("1.1.1.1", now=1)
    assert ratelimit.allow("2.2.2.2", now=1)
    assert ratelimit.allow("1.1.1.1", now=ratelimit.WINDOW_S + 1)


def test_forged_forwarded_header_does_not_change_the_address(client):  # noqa: F811
    for i in range(ratelimit.REPORTS_PER_WINDOW):
        client.post("/api/reports", json={"url": "http://example.com/", "claim": "c"},
                    headers={"X-Forwarded-For": f"9.9.9.{i}"})
    r = client.post("/api/reports", json={"url": URL_A, "claim": "c"}, headers={"X-Forwarded-For": "8.8.8.8"})
    assert r.status_code == 429


def test_full_queue_refuses_new_work(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ratelimit, "MAX_WAITING", 2)
    for _ in range(2):
        db.create_case(URL_A, "tailspin-offers.test", "c", {"id": "cust-1001", "name": "T"})
    assert client.post("/api/reports", json={"url": URL_A, "claim": "c"}).status_code == 503
    assert client.post("/api/test-url", json={"url": INTERNET}).status_code == 503
