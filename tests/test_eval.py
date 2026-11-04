"""The corpus is sound and the scoring counts what it says."""
import json
import re
from pathlib import Path

from app import inventory
from eval import metrics

ROOT = Path(__file__).parent.parent
CORPUS = json.loads((ROOT / "corpus" / "cases.json").read_text(encoding="utf-8"))
SERVER = (ROOT / "testsite" / "server.py").read_text(encoding="utf-8")


def label(id, abuse, owner="cust-1", injected=False, expected="Close"):
    return {"id": id, "abuse": abuse, "owner": owner, "injected": injected, "expected_action": expected}


def test_corpus_is_large_enough_and_covers_every_category():
    assert len(CORPUS) >= 20 and len({c["id"] for c in CORPUS}) == len(CORPUS)
    assert {c["category"] for c in CORPUS} == {"phishing", "legit_login", "legit_sso", "suspicious", "containment",
                                               "false_report", "injection", "not_customer"}


def test_owners_match_the_inventory():
    for c in CORPUS:
        customer = inventory.customer_for(inventory.reported_host(c["url"]))
        assert (customer["id"] if customer else None) == c["owner"], c["id"]


def test_every_customer_page_exists_on_the_test_site():
    for c in CORPUS:
        if c["owner"]:
            path = re.sub(r"^https?://[^/]+", "", c["url"])
            assert f'"{path}"' in SERVER, c["id"]


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
