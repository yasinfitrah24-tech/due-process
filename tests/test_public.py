"""Public modes and limits."""
import json

from app import db, ratelimit, signals
from tests.evidence import PAGE_A_EVIDENCE
from tests.test_model_pipeline import URL_A, client, log, replies, run  # noqa: F401

INTERNET = "http://example.com/login"


def submit_url(client):  # noqa: F811
    r = client.post("/api/test-url", json={"url": INTERNET})
    assert r.status_code == 202, r.text
    return client.get(f"/api/cases/{r.json()['ref']}").json()


def test_any_url_needs_no_inventory_and_has_no_target(client, monkeypatch):  # noqa: F811
    from app import pipeline, visitor
    from tests.evidence import visit_result
    monkeypatch.setattr(pipeline.visitor, "run_visit", lambda url, case, on_event=None: visit_result(json.loads(json.dumps(PAGE_A_EVIDENCE))))
    assert visitor is pipeline.visitor
    c = submit_url(client)
    assert c["mode"] == "test" and c["customer_id"] == "" and c["allowed_actions"] == []
    assert c["level"] == signals.PROVEN
    for action in ("Suspend", "Warn", "Close"):
        assert client.post(f"/api/cases/{c['ref']}/actions", json={"action": action}).status_code == 403
    assert db.get_case(c["id"])["decided_action"] is None


def test_any_url_runs_no_claim_extraction_or_documents(client, monkeypatch, log):  # noqa: F811
    from app import pipeline
    from tests.evidence import visit_result
    monkeypatch.setattr(pipeline.visitor, "run_visit", lambda url, case, on_event=None: visit_result(json.loads(json.dumps(PAGE_A_EVIDENCE))))
    submit_url(client)
    assert [e["role"] for e in log] == ["verdict_claim"]


def test_any_url_page_says_there_is_no_target(client, monkeypatch):  # noqa: F811
    from app import pipeline
    from tests.evidence import visit_result
    monkeypatch.setattr(pipeline.visitor, "run_visit", lambda url, case, on_event=None: visit_result(json.loads(json.dumps(PAGE_A_EVIDENCE))))
    page = client.get(f"/cases/{submit_url(client)['ref']}").text
    assert "no target and no action" in page and "not a finding of abuse" in page and "<h2>Decision</h2>" not in page


def fake_any_url(monkeypatch):
    from app import pipeline
    from tests.evidence import visit_result
    monkeypatch.setattr(pipeline.visitor, "run_visit", lambda url, case, on_event=None: visit_result(json.loads(json.dumps(PAGE_A_EVIDENCE))))


def test_any_url_is_reached_only_by_its_random_link(client, monkeypatch):  # noqa: F811
    fake_any_url(monkeypatch)
    c = submit_url(client)
    assert len(c["ref"]) >= 20 and not c["ref"].isdigit()
    assert client.get(f"/cases/{c['ref']}").status_code == 200
    for path in (f"/cases/{c['id']}", f"/api/cases/{c['id']}", f"/partials/cases/{c['id']}",
                 f"/cases/{c['id']}/shots/shot-0.jpg", "/cases/not-the-token", "/api/cases/not-the-token"):
        assert client.get(path).status_code == 404


def test_any_url_is_not_in_the_public_queue(client, monkeypatch):  # noqa: F811
    fake_any_url(monkeypatch)
    submit_url(client)
    assert INTERNET not in client.get("/").text and INTERNET not in client.get("/partials/queue").text
    run(client, URL_A)
    assert URL_A in client.get("/").text


def test_web_form_redirects_to_the_private_link(client, monkeypatch):  # noqa: F811
    fake_any_url(monkeypatch)
    r = client.post("/test-url", data={"url": INTERNET}, follow_redirects=False)
    assert r.status_code == 303 and not r.headers["location"].rsplit("/", 1)[1].isdigit()


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


def test_address_comes_from_the_header_only_from_caddy(client, monkeypatch):  # noqa: F811
    from app import main
    monkeypatch.setattr(main, "trusted_proxy_addresses", lambda: {"testclient"})
    for i in range(ratelimit.REPORTS_PER_WINDOW + 2):
        r = client.post("/api/reports", json={"url": "http://example.com/", "claim": "c"},
                        headers={"X-Forwarded-For": f"9.9.9.{i}"})
        assert r.status_code == 422


def test_last_forwarded_address_is_the_one_caddy_saw(client, monkeypatch):  # noqa: F811
    from app import main
    monkeypatch.setattr(main, "trusted_proxy_addresses", lambda: {"testclient"})
    for _ in range(ratelimit.REPORTS_PER_WINDOW):
        client.post("/api/reports", json={"url": "http://example.com/", "claim": "c"},
                    headers={"X-Forwarded-For": "1.1.1.1, 7.7.7.7"})
    r = client.post("/api/reports", json={"url": URL_A, "claim": "c"}, headers={"X-Forwarded-For": "2.2.2.2, 7.7.7.7"})
    assert r.status_code == 429


def test_full_queue_refuses_new_work(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ratelimit, "MAX_WAITING", 2)
    for _ in range(2):
        db.create_case(URL_A, "tailspin-offers.test", "c", {"id": "cust-1001", "name": "T"})
    assert client.post("/api/reports", json={"url": URL_A, "claim": "c"}).status_code == 503
    assert client.post("/api/test-url", json={"url": INTERNET}).status_code == 503


def test_front_page_explains_and_offers_every_example(client):  # noqa: F811
    from app.examples import EXAMPLES
    page = client.get("/").text
    assert "throwaway browser" in page and "no access code" in page and 'action="/reports"' in page
    for key in EXAMPLES:
        assert f'action="/try/{key}"' in page


def test_example_button_opens_a_case(client):  # noqa: F811
    r = client.post("/try/phishing", follow_redirects=False)
    assert r.status_code == 303 and db.get_case(1)["url"] == URL_A
    assert client.post("/try/nothing").status_code == 404


def test_example_button_counts_against_the_rate_limit(client):  # noqa: F811
    for _ in range(ratelimit.REPORTS_PER_WINDOW):
        client.post("/api/reports", json={"url": "http://example.com/", "claim": "c"})
    assert client.post("/try/phishing").status_code == 429


def test_examples_are_all_on_the_inventory_and_the_test_site():
    from pathlib import Path

    from app import inventory
    from app.examples import EXAMPLES
    server = (Path(__file__).parent.parent / "testsite" / "server.py").read_text(encoding="utf-8")
    for e in EXAMPLES.values():
        assert inventory.customer_for(inventory.reported_host(e["url"]))
        assert '"' + e["url"].split("/", 3)[3].join(["/", ""]) + '"' in server


def test_reset_demo_replaces_every_case_and_keeps_the_ledger(client, monkeypatch):  # noqa: F811
    from app import pipeline, reset_demo
    from app.examples import EXAMPLES
    from tests.evidence import PAGE_C_EVIDENCE, visit_result
    monkeypatch.setattr(pipeline.visitor, "run_visit", lambda url, case, on_event=None: visit_result(json.loads(json.dumps(PAGE_C_EVIDENCE))))
    run(client, URL_A)
    db.record_usage(None, "seed", "m", 0, 0, 1.5, 0)
    client.post("/api/test-url", json={"url": INTERNET})
    reset_demo.main()
    cases = db.list_cases()
    assert sorted(c["id"] for c in cases) == list(range(1, len(EXAMPLES) + 1))
    assert [c["url"] for c in reversed(cases)] == [e["url"] for e in EXAMPLES.values()]
    assert {c["status"] for c in cases} == {"ready"} and db.total_cost() == 1.5
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM cases WHERE mode = 'test'").fetchone()[0] == 0


def test_eval_hosts_are_not_listed_but_still_work(client, monkeypatch):  # noqa: F811
    page = client.get("/").text
    assert "orchard-site.test" not in page and "birch-site.test" not in page and "tailspin-offers.test" in page
    from app import pipeline
    from tests.evidence import PAGE_C_EVIDENCE, visit_result
    monkeypatch.setattr(pipeline.visitor, "run_visit", lambda url, case, on_event=None: visit_result(json.loads(json.dumps(PAGE_C_EVIDENCE))))
    r = client.post("/api/reports", json={"url": "http://orchard-site.test/p/1042", "claim": "c"})
    assert r.status_code == 202 and r.json()["customer_id"] == "cust-1004"


def test_reset_does_not_pass_old_model_calls_to_new_cases(client):  # noqa: F811
    from app import ledger
    for _ in range(ledger.MAX_CALLS_PER_CASE):
        db.record_usage(1, "documents", "m", 0, 0, 0.25, 0)
    assert db.case_calls(1) == ledger.MAX_CALLS_PER_CASE
    db.clear_cases()
    assert db.case_calls(1) == 0 and db.total_cost() == 1.5
    c = run(client, URL_A)
    assert c["id"] == 1 and {o["status"] for o in c["outputs"].values()} == {"accepted"}
