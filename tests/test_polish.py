"""Parallel model roles, the case summary and the sentences code writes."""
import copy
import json
import threading

import pytest

from agent import baseline, roles
from app import db, explain, ledger, letters, main, pipeline
from tests.evidence import PAGE_A_EVIDENCE, PAGE_C_EVIDENCE, visit_result
from tests.test_model_pipeline import Fake, Priced, URL_A, URL_B, URL_C, URL_D, client, log, replies, run, spend  # noqa: F401


def signals(**on):
    names = ("form_to_foreign_host", "password_field", "redirect_count", "final_host_differs", "hidden_text_present",
             "egress_blocked", "download_quarantined")
    return [{"id": n, "value": on.get(n, False)} for n in names]


class Slow(Priced):
    """Counts calls."""
    lock = threading.Lock()

    def __init__(self):
        self.calls = 0

    def complete(self, messages, role):
        with self.lock:
            self.calls += 1
        return {"text": "{}", "prompt_tokens": 1, "completion_tokens": 1}


def in_parallel(count, job):
    barrier = threading.Barrier(count)
    results = []

    def work():
        barrier.wait(timeout=10)
        try:
            results.append(job())
        except ledger.Refused as e:
            results.append(str(e))

    threads = [threading.Thread(target=work) for _ in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_roles_really_run_side_by_side(client, monkeypatch, log, replies):  # noqa: F811
    barrier = threading.Barrier(4, timeout=10)
    plain = Fake.complete

    def meet(self, messages, role):
        barrier.wait()  # breaks, and fails the role, unless all four are in flight at once
        return plain(self, messages, role)

    monkeypatch.setattr(Fake, "complete", meet)
    c = run(client, URL_A)
    assert {o["status"] for o in c["outputs"].values()} == {"accepted"}


def test_case_limit_holds_under_concurrency(client):  # noqa: F811
    fake = Slow()
    results = in_parallel(12, lambda: ledger.call(fake, 1, "verdict_claim", []))
    assert fake.calls == ledger.MAX_CALLS_PER_CASE
    assert sum(isinstance(r, str) and "case limit" in r for r in results) == 12 - ledger.MAX_CALLS_PER_CASE
    assert db.case_calls(1) == ledger.MAX_CALLS_PER_CASE


def test_appeal_limit_holds_under_concurrency(client):  # noqa: F811
    fake = Slow()
    in_parallel(8, lambda: ledger.call(fake, 1, "appeal_letter:1", []))
    assert fake.calls == ledger.MAX_CALLS_PER_APPEAL


def test_cost_limit_holds_under_concurrency(client):  # noqa: F811
    """Each call may cost up to USD 0.002, and room is left for three. While those three are in flight, their reserved
    worst cost must stop the other five."""
    spend(ledger.COST_LIMIT_USD - 0.0065, "2026-01-01T00:00:00+00:00")
    fake = Slow()
    release = threading.Event()
    refused = threading.Semaphore(0)
    plain = fake.complete

    def held(messages, role):
        assert release.wait(timeout=20)
        return plain(messages, role)

    def attempt(case_id):
        try:
            ledger.call(fake, case_id, "verdict_claim", [])
        except ledger.Refused:
            refused.release()

    fake.complete = held
    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for _ in range(5):
        assert refused.acquire(timeout=15)
    release.set()
    for t in threads:
        t.join()
    assert fake.calls == 3


def test_reservation_becomes_the_real_cost(client):  # noqa: F811
    out = ledger.call(Priced(), 1, "verdict_claim", [])
    assert db.total_cost() == pytest.approx(out["cost_usd"])


def test_failed_call_costs_nothing_and_still_counts(client):  # noqa: F811
    class Broken(Priced):
        def complete(self, messages, role):
            raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        ledger.call(Broken(), 1, "verdict_claim", [])
    assert db.total_cost() == 0 and db.case_calls(1) == 1


def test_document_retry_still_works_next_to_other_roles(client, replies, log):  # noqa: F811
    replies["documents"] = ["not json"]
    c = run(client, URL_A)
    assert sum(e["role"] == "documents" for e in log) == 2
    assert {o["status"] for o in c["outputs"].values()} == {"accepted"}
    assert c["usage"]["calls"] == 5


@pytest.mark.parametrize("on,text", [
    ({"form_to_foreign_host": True, "password_field": True},
     "A form on the page sends data to another host, and the page asks for a password."),
    ({"form_to_foreign_host": True}, "A form on the page sends data to another host. The page asks for no password."),
    ({"password_field": True}, "The page asks for a password, and no form sends data to another host."),
    ({}, "The visit found no form that sends data to another host."),
])
def test_why_follows_the_signals(on, text):
    assert explain.why(signals(**on)) == text


def test_why_adds_at_most_one_sentence():
    text = explain.why(signals(form_to_foreign_host=True, password_field=True, hidden_text_present=True, egress_blocked=True,
                               download_quarantined=True))
    assert text.count(".") == 2 and "hidden text" in text and "started a download" in text


def test_why_without_evidence_is_nothing():
    assert explain.why(None) is None and explain.why([]) is None


def test_summary_comes_before_everything_else(client):  # noqa: F811
    c = run(client, URL_A)
    page = client.get(f"/cases/{c['id']}").text
    order = ["badge-Proven", "Target:", "A form on the page sends data", 'value="Suspend"', "Evidence statement",
             "Reporter alleges vs evidence shows", "What the vision model reads", "<h2>Drafts</h2>", "Technical evidence",
             "<h2>Signals</h2>", "<h2>Sandbox receipt</h2>", "<h2>Audit log</h2>"]
    positions = [page.index(x) for x in order]
    assert positions == sorted(positions)


def test_running_case_shows_progress_above_and_no_actions(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(main, "submit", lambda case_id: None)
    case_id = client.post("/api/reports", json={"url": URL_A, "claim": "c"}).json()["case_id"]
    db.add_event(case_id, None, {"source": "browser", "kind": "navigation", "url": "http://x.test/"})
    page = client.get(f"/cases/{case_id}").text
    assert page.index("Live progress") < page.index("Technical evidence")
    assert "<h2>Timeline</h2>" not in page and 'value="Suspend"' not in page


def test_finished_case_moves_the_timeline_into_the_details(client, monkeypatch):  # noqa: F811
    c = run(client, URL_A)
    page = client.get(f"/cases/{c['id']}").text
    assert page.index("Technical evidence") < page.index("<h2>Timeline</h2>") and "Live progress" not in page


def test_behavior_row_is_a_sentence_not_ids(client, replies):  # noqa: F811
    replies["claim_extraction"] = json.dumps({"category": "phishing", "alleged_brand": None, "alleged_behavior": "x"})
    c = run(client, URL_A)
    row = next(r for r in c["comparison"] if r["label"] == "Behavior")
    assert row["shows"].startswith("A form on the page sends data") and "_" not in row["shows"]


def test_notice_closes_with_a_sentence_from_code(client):  # noqa: F811
    c = run(client, URL_A)
    client.post(f"/api/cases/{c['id']}/actions", json={"action": "Suspend"})
    client.post(f"/cases/{c['id']}/documents/customer_notice/send")
    d = client.get(f"/api/cases/{c['id']}").json()
    assert f"{letters.APPEAL_CLOSING}\nhttp" in d["sent"]["customer_notice"]
    assert d["sent"]["customer_notice"].endswith(f"/appeal/{d['appeal_token']}")


@pytest.mark.parametrize("text", ["You can appeal this decision.", "To dispute this, reply here.", "Your appeal is welcome."])
def test_the_model_may_not_write_about_appeals(text):
    s = {"text": text, "signals": [], "factual": False}
    draft = json.dumps({"statement_of_reasons": [s], "customer_notice": [s], "reporter_reply": [s]})
    with pytest.raises(roles.Rejected):
        roles.check_documents(draft, "Proven", ["password_field"])


def customer_case(client, monkeypatch, fixed=None):  # noqa: F811
    monkeypatch.setattr(pipeline.visitor, "run_visit", lambda url, case, on_event=None: visit_result(
        copy.deepcopy(PAGE_C_EVIDENCE if "appeal" in case else PAGE_A_EVIDENCE)))
    monkeypatch.setattr(pipeline.visitor, "simulate_fix", (fixed if fixed is not None else []).append)
    monkeypatch.setattr(main, "submit_appeal", pipeline.revisit)
    case_id = client.post("/try/fixable", follow_redirects=False).headers["location"].rsplit("/", 1)[1]
    client.post(f"/api/cases/{case_id}/actions", json={"action": "Suspend"})
    return client.get(f"/api/cases/{case_id}").json()


def test_fix_is_confirmed_and_the_button_is_disabled(client, monkeypatch):  # noqa: F811
    fixed = []
    c = customer_case(client, monkeypatch, fixed)
    client.post(f"/appeal/{c['appeal_token']}/simulate-fix")
    page = client.get(f"/appeal/{c['appeal_token']}").text
    assert "Fix applied." in page and "disabled" in page and "/simulate-fix" not in page
    client.post(f"/appeal/{c['appeal_token']}/simulate-fix")
    assert len(fixed) == 1


def test_customer_sees_the_decision_and_the_letter(client, monkeypatch):  # noqa: F811
    c = customer_case(client, monkeypatch)
    token = c["appeal_token"]
    client.post(f"/appeal/{token}", data={"choice": "fixed"})
    assert "An analyst is reviewing" in client.get(f"/appeal/{token}").text
    a = client.get(f"/api/cases/{c['id']}").json()["appeals"][0]
    client.post(f"/cases/{c['id']}/appeals/{a['id']}/decide", data={"decision": "Reinstate"})
    page = client.get(f"/appeal/{token}").text
    assert "Decision: Reinstated" in page and "has not sent the letter yet" in page
    client.post(f"/cases/{c['id']}/appeals/{a['id']}/send")
    page = client.get(f"/appeal/{token}").text
    assert "Result of the second visit: Not proven" in page and "has not sent the letter yet" not in page


def test_customer_sees_kept(client, monkeypatch):  # noqa: F811
    c = customer_case(client, monkeypatch)
    client.post(f"/appeal/{c['appeal_token']}", data={"choice": "contested"})
    a = client.get(f"/api/cases/{c['id']}").json()["appeals"][0]
    client.post(f"/cases/{c['id']}/appeals/{a['id']}/decide", data={"decision": "Keep"})
    assert "Decision: Kept." in client.get(f"/appeal/{c['appeal_token']}").text


def test_customer_view_links_back_with_a_demo_label(client, monkeypatch):  # noqa: F811
    c = customer_case(client, monkeypatch)
    page = client.get(f"/appeal/{c['appeal_token']}").text
    assert f"Back to the analyst view (case #{c['id']})" in page and "Demo:" in page


def test_eval_baseline_prompt_has_no_reason_field():
    plain = baseline.messages("http://x.test/", "r", [])[0]["content"]
    assert "reason" not in plain
    assert '"reason"' in baseline.messages("http://x.test/", "r", [], explain=True)[0]["content"]


def test_comparison_shows_the_baseline_reason_and_the_title(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(pipeline.visitor, "run_visit", lambda url, case, on_event=None: visit_result(copy.deepcopy(PAGE_C_EVIDENCE)))
    monkeypatch.setattr(main, "submit_baseline", pipeline.run_baseline)
    location = client.post("/compare/run/login", follow_redirects=False).headers["location"]
    page = client.get(location).text
    assert "Same report text, different pages" in page
    assert page.count("Reason given by the baseline: Mock reason.") == 2


def test_baseline_reason_is_escaped_and_short():
    answer = baseline.parse(json.dumps({"action": "close", "customer_id": None, "reason": "x" * 999}))
    assert len(answer["reason"]) == 300
