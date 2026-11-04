import copy

import pytest

from app import policy, signals
from tests.evidence import PAGE_A_EVIDENCE, PAGE_B_EVIDENCE, PAGE_C_EVIDENCE, dest


def by_id(found):
    return {s["id"]: s for s in found}


def test_page_a_is_proven():
    found = signals.compute(PAGE_A_EVIDENCE, "http://tailspin-offers.test/start")
    s = by_id(found)
    assert signals.level(found) == signals.PROVEN
    assert s["form_to_foreign_host"]["detail"] == ["http://collector.test/submit"]
    assert s["form_to_foreign_host"]["kind"] == "declared_in_script"
    assert s["redirect_count"]["value"] == 2 and s["final_host_differs"]["value"] is True
    assert s["hidden_text_present"]["value"] is True


def test_page_b_password_alone_is_not_proven():
    found = signals.compute(PAGE_B_EVIDENCE, "http://northwind-bank.test/login")
    assert by_id(found)["password_field"]["value"] is True
    assert signals.level(found) == signals.NOT_PROVEN


def test_page_c_is_not_proven():
    found = signals.compute(PAGE_C_EVIDENCE, "http://northwind-bank.test/branches")
    assert signals.level(found) == signals.NOT_PROVEN


def test_foreign_form_without_password_is_suspicious():
    evidence = {**copy.deepcopy(PAGE_A_EVIDENCE), "password_fields": []}
    assert signals.level(signals.compute(evidence, "http://tailspin-offers.test/start")) == signals.SUSPICIOUS


@pytest.mark.parametrize("change", [
    {"redirects": [{"type": "http", "from": "http://a.test/", "to": "http://b.test/", "kind": "observed"}] * 5},
    {"final_url": "http://elsewhere.test/"},
    {"hidden_text": [{"page": "p", "text": "x", "reason": "display:none", "kind": "observed"}]},
])
def test_other_signals_do_not_raise_the_level(change):
    evidence = {**copy.deepcopy(PAGE_B_EVIDENCE), **change}
    assert signals.level(signals.compute(evidence, "http://northwind-bank.test/login")) == signals.NOT_PROVEN


def test_observed_form_action_outranks_declared():
    evidence = copy.deepcopy(PAGE_A_EVIDENCE)
    evidence["destinations"].append(dest("form-action", "observed", "p", "http://x.test/", False))
    assert by_id(signals.compute(evidence, "http://t.test/"))["form_to_foreign_host"]["kind"] == "observed"


def test_foreign_request_outside_a_form_is_not_a_form_signal():
    evidence = copy.deepcopy(PAGE_C_EVIDENCE)
    evidence["destinations"].append(dest("request:fetch", "observed", "p", "http://cdn.test/x", False, None))
    assert by_id(signals.compute(evidence, "http://t.test/"))["form_to_foreign_host"]["value"] is False


@pytest.mark.parametrize("level,allowed", [
    (signals.NOT_PROVEN, ("Close",)),
    (signals.SUSPICIOUS, ("Warn", "Close")),
    (signals.PROVEN, ("Suspend", "Warn", "Close")),
    (None, ()),
])
def test_allowed_actions(level, allowed):
    assert policy.allowed_actions(level) == allowed
