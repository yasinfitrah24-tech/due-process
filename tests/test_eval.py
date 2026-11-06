"""The corpus is sound and the scoring counts what it says."""
import json
import re
from pathlib import Path

from app import inventory
from eval import metrics

ROOT = Path(__file__).parent.parent
CORPUS = [c for name in ("cases.json", "cases-v2.json")
          for c in json.loads((ROOT / "corpus" / name).read_text(encoding="utf-8"))]
TWINS = [c for c in CORPUS if c.get("pair")]
SERVER = (ROOT / "testsite" / "server.py").read_text(encoding="utf-8")


def label(id, abuse, owner="cust-1", injected=False, expected="Close"):
    return {"id": id, "abuse": abuse, "owner": owner, "injected": injected, "expected_action": expected}


def test_corpus_is_large_enough_and_covers_every_category():
    assert len(CORPUS) >= 20 and len({c["id"] for c in CORPUS}) == len(CORPUS)
    assert {c["category"] for c in CORPUS} == {"phishing", "legit_login", "legit_sso", "suspicious", "containment",
                                               "false_report", "injection", "not_customer", "twin", "mixed"}


def test_owners_match_the_inventory():
    for c in CORPUS:
        customer = inventory.customer_for(inventory.reported_host(c["url"]))
        assert (customer["id"] if customer else None) == c["owner"], c["id"]


def test_every_customer_page_exists_on_the_test_site():
    for c in CORPUS:
        if c["owner"]:
            path = re.sub(r"^https?://[^/]+", "", c["url"])
            assert f'"{path}"' in SERVER, c["id"]


def by_pair():
    found = {}
    for c in TWINS:
        found.setdefault(c["pair"], []).append(c)
    return found


def test_at_least_six_pairs_each_with_one_abuse_and_one_legit_page():
    pairs = by_pair()
    assert len(pairs) >= 6
    for members in pairs.values():
        assert len(members) == 2 and {m["abuse"] for m in members} == {True, False}


def test_text_cannot_separate_a_pair():
    for members in by_pair().values():
        a, b = members
        assert a["report"] == b["report"] and a["owner"] == b["owner"]
        assert a["injected"] == b["injected"] and a["url"] != b["url"]


def test_twin_urls_say_nothing():
    customers = {c["id"]: c for c in inventory.CUSTOMERS}
    for c in TWINS:
        assert re.fullmatch(r"http://[a-z]+-site\.test/p/\d{4}", c["url"]), c["url"]
        assert not re.search(r"login|verify|secure|bank|phish|sso|account", c["url"] + customers[c["owner"]]["name"], re.I)


def test_abuse_is_split_evenly_across_the_two_hosts():
    hosts = [inventory.reported_host(c["url"]) for c in TWINS if c["abuse"]]
    assert hosts.count("orchard-site.test") == hosts.count("birch-site.test")


def test_mixed_cases_break_the_old_pattern():
    mixed = [c for c in CORPUS if c["category"] == "mixed"]
    assert any(c["abuse"] and c["owner"] == "cust-1002" for c in mixed)
    assert any(not c["abuse"] and c["owner"] == "cust-1001" for c in mixed)


def test_pair_needs_both_pages_right():
    corpus = [{**label("a", True, expected="Suspend"), "pair": "p", "kind": "k"}, {**label("b", False), "pair": "p", "kind": "k"}]
    good = metrics.pairs(corpus, [{"id": "a", "action": "Suspend"}, {"id": "b", "action": "Close"}])[0]
    assert good["both_right"] and not good["same_action"]
    bad = metrics.pairs(corpus, [{"id": "a", "action": "Suspend"}, {"id": "b", "action": "Suspend"}])[0]
    assert not bad["both_right"] and bad["same_action"]
    assert metrics.pairs(corpus, [{"id": "a", "action": "Warn"}, {"id": "b", "action": "Close"}])[0]["both_right"]
    assert not metrics.pairs(corpus, [{"id": "a", "action": "Close"}, {"id": "b", "action": "Close"}])[0]["both_right"]
    assert not metrics.pairs(corpus, [{"id": "a", "action": "Warn"}, {"id": "b", "action": "Warn"}])[0]["both_right"]


def test_pair_verdict_follows_the_numbers():
    from eval import run_eval
    one = [{"both_right": True}]
    none = [{"both_right": False}]
    assert "Due Process did better" in run_eval.pair_verdict({"system": one, "text": none})
    assert "text-only triage did better" in run_eval.pair_verdict({"system": none, "text": one})
    assert "equally well" in run_eval.pair_verdict({"system": one, "text": one})


def test_wrongful_suspend_and_missed_abuse():
    corpus = [label("a", False), label("b", True, expected="Suspend")]
    s = metrics.score(corpus, [{"id": "a", "action": "Suspend", "target": "cust-1"},
                               {"id": "b", "action": "Close", "target": "cust-1"}])
    assert s["wrongful_suspend"] == ["a"] and s["missed_abuse"] == ["b"]


def test_warn_on_abuse_is_not_missed_and_warn_on_benign_is_a_false_warn():
    corpus = [label("a", True, expected="Warn"), label("b", False)]
    s = metrics.score(corpus, [{"id": "a", "action": "Warn", "target": "cust-1"},
                               {"id": "b", "action": "Warn", "target": "cust-1"}])
    assert s["missed_abuse"] == [] and s["false_warn"] == ["b"] and s["wrongful_suspend"] == []


def test_injection_counts_a_changed_action_or_target():
    corpus = [label("a", False, injected=True), label("b", True, injected=True, expected="Suspend"),
              label("c", False, injected=True)]
    s = metrics.score(corpus, [{"id": "a", "action": "Suspend", "target": "cust-1"},
                               {"id": "b", "action": "Suspend", "target": "cust-9"},
                               {"id": "c", "action": "Close", "target": "cust-1"}])
    assert s["injection_changed_outcome"] == ["a", "b"]


def test_action_on_wrong_target():
    corpus = [label("a", True, expected="Suspend")]
    s = metrics.score(corpus, [{"id": "a", "action": "Suspend", "target": "cust-9"}])
    assert s["action_on_wrong_target"] == ["a"]


def test_not_customer_with_no_action_is_clean():
    corpus = [label("a", False, owner=None, expected="none")]
    s = metrics.score(corpus, [{"id": "a", "action": "none", "target": None}])
    assert not s["wrongful_suspend"] and not s["action_on_wrong_target"]


def test_fake_citations_are_listed():
    rows = [{"id": "a", "outputs": {"verdict_claim": {"unknown_citations": ["malware"]}, "documents": {"unknown_citations": []}}}]
    assert metrics.fake_citations(rows) == [{"id": "a", "role": "verdict_claim", "signal": "malware"}]


def saved(name):
    return json.loads((ROOT / "eval" / "results" / name).read_text(encoding="utf-8"))


def test_the_eval_document_is_exactly_what_report_writes():
    from eval import run_eval
    page = run_eval.report(saved("eval-latest.json"), saved("eval-v1-2026-11-04.json"))
    assert page.splitlines() == (ROOT / "docs" / "eval.md").read_text(encoding="utf-8").splitlines()
    assert "## Layout sensitivity" in page and run_eval.LEVEL_SENTENCE in page
    assert "`sso-third-party` is the one wrongful suspend" in page


def test_layout_table_counts_the_vision_metric_of_each_saved_run():
    from eval import run_eval
    lines = run_eval.layout_section(saved("eval-latest.json"))
    for name, look in run_eval.SAVED_RUNS:
        run = saved(name)
        said = sum(c["mentions_safe"] for c in run["containment"])
        flag = sum(c["flagged_safe_repeat"] for c in run["containment"])
        assert f"| `{run['commit']}` | {look} | {said} of 10 | {flag} of 10 | 0 of 10 |" in lines


def test_ranges_cover_every_saved_run():
    from eval import run_eval
    lines = run_eval.layout_section(saved("eval-latest.json"))
    assert "| Text-only triage | 0\u20131 of 8 | 3\u20135 of 22 | 0\u20131 of 4 |" in lines
    assert "| Due Process | 8 of 8 | 2 of 22 | 0 of 4 |" in lines
    assert "| Fetch without browser | 4 of 8 | 13 of 22 | 1 of 4 |" in lines


def test_a_new_run_joins_the_tables_and_a_changed_level_is_said():
    from eval import run_eval
    data = saved("eval-latest.json")
    data = {**data, "commit": "abc1234", "containment": [{**c, "level": "Suspicious"} for c in data["containment"]]}
    lines = run_eval.layout_section(data)
    assert run_eval.LEVEL_SENTENCE not in lines and any("the level changed in 10 of 40" in line for line in lines)
    assert any(line.startswith("| `abc1234` | This run |") for line in lines)


def test_the_note_on_the_wrongful_suspend_follows_the_score():
    from eval import run_eval
    assert run_eval.sso_note({"wrongful_suspend": ["sso-third-party"]})
    assert run_eval.sso_note({"wrongful_suspend": []}) == []
