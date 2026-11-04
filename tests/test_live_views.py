"""Timeline, live progress and One report, two pages, with fake sandboxes and fake models."""
import copy
import io
import json
from pathlib import Path

import pytest

from agent import baseline
from app import db, main, pipeline, ratelimit, timeline
from app.twins import TWINS
from sandbox import launch
from tests.evidence import PAGE_A_EVIDENCE, PAGE_C_EVIDENCE, visit_result
from tests.test_model_pipeline import URL_A, client, log, replies, run  # noqa: F401

CORPUS = json.loads((Path(__file__).parent.parent / "corpus" / "cases-v2.json").read_text(encoding="utf-8"))


def event(kind, ts, source="browser", appeal_id=None, id=1, **detail):
    return {"id": id, "ts": ts, "kind": kind, "source": source, "appeal_id": appeal_id, "detail": detail}


def test_rows_are_in_time_order_with_offsets_per_visit():
    rows = timeline.rows([event("page_loaded", 12.5, url="u"), event("navigation", 10.0, url="u"),
                          event("egress_blocked", 11.0, "proxy", method="GET", host="h", port=80, reason="r"),
                          event("navigation", 100.0, appeal_id=1, url="u")])
    assert [r["at"] for r in rows] == [0.0, 1.0, 2.5, 0.0]
    assert [r["source"] for r in rows] == ["browser", "proxy", "browser", "browser"]
    assert rows[1]["blocked"] and "Proxy blocked GET h:80: r" == rows[1]["text"]
    assert rows[3]["visit"] == "second visit"


def test_unknown_or_incomplete_events_do_not_break_the_page():
    assert timeline.describe(event("something_new", 1)) == "something_new"
    assert timeline.describe(event("navigation", 1)) == "Browser opened {url}"


@pytest.mark.parametrize("line,kind", [
    ('DPEVENT {"kind": "navigation", "url": "u"}', "navigation"),
    ("[pid=1] some chromium noise", None),
    ("DPEVENT not json", None),
    ('DPEVENT ["no kind"]', None),
])
def test_parse_event(line, kind):
    parsed = launch.parse_event(line)
    assert (parsed or {}).get("kind") == kind


def test_proxy_lines_become_events():
    e = launch.proxy_event({"ts": 1.0, "decision": "blocked", "method": "GET", "host": "h", "port": 80, "reason": "r"})
    assert e["kind"] == "egress_blocked" and e["source"] == "proxy"
    assert launch.proxy_event({"decision": "other"}) is None


def test_the_launcher_passes_on_a_bounded_number_of_events(monkeypatch):
    lines = [f'DPEVENT {{"kind": "navigation", "url": "{i}"}}\n'.encode() for i in range(launch.MAX_EVENTS + 50)]

    class Proc:
        stdout, stderr, returncode = io.BytesIO(b"{}"), iter(lines), 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(launch.subprocess, "Popen", lambda *a, **k: Proc())
    monkeypatch.setattr(launch, "teardown", lambda cid: {"container_gone": True, "forced_removal": False, "checked_at": "t"})
    monkeypatch.setattr(launch, "egress_log", lambda *a: [])
    seen = []
    launch.run_visit("http://x.test/", "flood", seen.append)
    assert len(seen) == launch.MAX_EVENTS


def visit_with_events(url, case, on_event=None):
    for e in ({"ts": 1000.0, "source": "launcher", "kind": "sandbox_requested"},
              {"ts": 1001.0, "source": "browser", "kind": "navigation", "url": url},
              {"ts": 1001.5, "source": "proxy", "kind": "egress_allowed", "method": "GET", "host": "live", "port": 80},
              {"ts": 1002.0, "source": "launcher", "kind": "container_gone", "container_id": "abc"}):
        on_event(e)
    evidence = copy.deepcopy(PAGE_A_EVIDENCE)
    evidence["egress"] = [{"ts": 1001.6, "decision": "blocked", "method": "GET", "host": "169.254.169.254", "port": 80,
                           "reason": "cloud metadata address"}]
    return visit_result(evidence)


def test_case_page_shows_real_events_and_the_proxy_record_replaces_the_live_lines(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(pipeline.visitor, "run_visit", visit_with_events)
    c = run(client, URL_A)
    texts = [t["text"] for t in c["timeline"]]
    assert "Proxy blocked GET 169.254.169.254:80: cloud metadata address" in texts
    assert not any("live" in t for t in texts)
    assert texts.index("Asked the launcher for a new sandbox") < texts.index("Container gone: abc")
    assert any(t.startswith("Level computed by code: Proven") for t in texts)
    assert any(t.startswith("Model call finished: verdict_claim") for t in texts)
    page = client.get(f"/cases/{c['id']}").text
    assert "<h2>Timeline</h2>" in page


def test_no_events_means_no_timeline(client):  # noqa: F811
    c = run(client, URL_A)
    assert [t for t in c["timeline"] if t["source"] in ("browser", "proxy", "launcher")] == []


def test_a_running_case_says_live_progress_and_polls(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(main, "submit", lambda case_id: None)
    r = client.post("/api/reports", json={"url": URL_A, "claim": "c"})
    case_id = r.json()["case_id"]
    db.add_event(case_id, None, {"source": "browser", "kind": "navigation", "url": "http://x.test/"})
    page = client.get(f"/partials/cases/{case_id}").text
    assert "Live progress" in page and 'hx-trigger="every 1s"' in page and "Browser opened http://x.test/" in page


def test_page_text_in_events_is_escaped(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(main, "submit", lambda case_id: None)
    case_id = client.post("/api/reports", json={"url": URL_A, "claim": "c"}).json()["case_id"]
    db.add_event(case_id, None, {"source": "browser", "kind": "download", "name": "<script>alert(1)</script>"})
    page = client.get(f"/cases/{case_id}").text
    assert "<script>alert(1)</script>" not in page


def test_appeal_events_are_marked_as_the_second_visit(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(pipeline.visitor, "run_visit", visit_with_events)
    monkeypatch.setattr(main, "submit_appeal", pipeline.revisit)
    c = run(client, URL_A)
    client.post(f"/api/cases/{c['id']}/actions", json={"action": "Suspend"})
    token = client.get(f"/api/cases/{c['id']}").json()["appeal_token"]
    client.post(f"/appeal/{token}", data={"choice": "fixed"})
    rows = client.get(f"/api/cases/{c['id']}").json()["timeline"]
    assert {r["visit"] for r in rows} == {"first visit", "second visit"}
    assert "Proxy blocked" in " ".join(r["text"] for r in rows if r["visit"] == "second visit")


def test_reset_clears_events_and_comparisons(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(pipeline.visitor, "run_visit", visit_with_events)
    run(client, URL_A)
    db.clear_cases()
    assert db.case_events(1) == []


# One report, two pages

@pytest.fixture
def twins(client, monkeypatch):  # noqa: F811
    pages = {t["abuse_url"]: PAGE_A_EVIDENCE for t in TWINS.values()}
    pages.update({t["legit_url"]: PAGE_C_EVIDENCE for t in TWINS.values()})
    monkeypatch.setattr(pipeline.visitor, "run_visit",
                        lambda url, case, on_event=None: visit_result(copy.deepcopy(pages[url])))
    monkeypatch.setattr(main, "submit_baseline", pipeline.run_baseline)
    return client


def start(client, key="login"):  # noqa: F811
    r = client.post(f"/compare/run/{key}", follow_redirects=False)
    assert r.status_code == 303, r.text
    return r.headers["location"]


def test_twins_match_the_corpus():
    by_url = {c["url"]: c for c in CORPUS}
    for t in TWINS.values():
        abuse, legit = by_url[t["abuse_url"]], by_url[t["legit_url"]]
        assert abuse["pair"] == legit["pair"] and abuse["abuse"] and not legit["abuse"]
        assert abuse["report"] == legit["report"] == t["report"]


def test_one_report_goes_to_both_pages_with_a_labeled_baseline(twins):
    page = twins.get(start(twins)).text
    assert page.count("baseline: reads report text only") == 2
    assert page.count("Due Process") >= 2 and "Verdict" in page
    assert "Proven" in page and "Not proven" in page and "Decision: <strong>Suspend</strong>" in page


def test_baseline_gets_the_report_and_url_and_nothing_from_a_visit(twins, log):
    start(twins)
    sent = [e for e in log if e["role"] == "text_triage"]
    assert len(sent) == 2
    for e in sent:
        text = json.dumps(e["messages"])
        assert "Reported URL" in text and TWINS["login"]["report"] in text
        assert "form_to_foreign_host" not in text and "collector.test" not in text


def test_baseline_prompt_is_the_one_the_eval_uses():
    stage = (Path(__file__).parent.parent / "eval" / "stage_app.py").read_text(encoding="utf-8")
    assert "baseline.messages(" in stage and "baseline.parse(" in stage


def test_comparison_cases_cannot_be_acted_on_and_are_not_listed(twins):
    location = start(twins)
    with db.connect() as c:
        rows = c.execute("SELECT id, token, url FROM cases WHERE mode = 'twin'").fetchall()
    assert len(rows) == 2
    home = twins.get("/").text
    for row in rows:
        assert row["url"] not in home
        assert twins.get(f"/cases/{row['id']}").status_code == 404
        assert twins.post(f"/api/cases/{row['token']}/actions", json={"action": "Suspend"}).status_code == 403
        assert "<h2>Decision</h2>" not in twins.get(f"/cases/{row['token']}").text
    assert location.startswith("/compare/")


def test_comparison_needs_its_token(twins):
    assert twins.get("/compare/not-a-token").status_code == 404
    assert twins.post("/compare/run/nothing").status_code == 404


def test_strict_rate_limit(twins):
    for _ in range(ratelimit.COMPARES_PER_WINDOW):
        start(twins)
    assert twins.post("/compare/run/login").status_code == 429


def test_failed_baseline_is_shown_as_such(twins, replies):  # noqa: F811
    replies["text_triage"] = "not json"
    assert "No valid answer" in twins.get(start(twins)).text


def test_page_says_what_it_is_not(twins):
    page = twins.get("/compare").text
    assert "not how often real reports look like this" in page
    assert 'action="/compare/run/login"' in page


def test_parse_baseline():
    assert baseline.parse('```json\n{"action": "warn", "customer_id": "c"}\n```') == {"action": "Warn", "target": "c",
                                                                                     "reason": ""}
