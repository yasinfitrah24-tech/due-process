import copy

import pytest

from app import inventory, policy, signals
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


@pytest.mark.parametrize("evidence", [PAGE_A_EVIDENCE, PAGE_B_EVIDENCE, PAGE_C_EVIDENCE])
def test_blocked_egress_and_downloads_are_recorded_and_do_not_move_the_level(evidence):
    url = "http://northwind-bank.test/login"
    hostile = {**copy.deepcopy(evidence),
               "egress": [{"host": "169.254.169.254", "decision": "blocked", "reason": "cloud metadata address"}],
               "downloads": [{"suggested_filename": "a.exe"}]}
    found = signals.compute(hostile, url)
    assert by_id(found)["egress_blocked"]["value"] and by_id(found)["download_quarantined"]["value"]
    assert signals.level(found) == signals.level(signals.compute(evidence, url))


def test_observed_form_action_outranks_declared():
    evidence = copy.deepcopy(PAGE_A_EVIDENCE)
    evidence["destinations"].append(dest("form-action", "observed", "http://p.test/", "http://x.test/", False))
    assert by_id(signals.compute(evidence, "http://t.test/"))["form_to_foreign_host"]["kind"] == "observed"


def test_foreign_request_outside_a_form_is_not_a_form_signal():
    evidence = copy.deepcopy(PAGE_C_EVIDENCE)
    evidence["destinations"].append(dest("request:fetch", "observed", "http://p.test/", "http://cdn.test/x", False, None))
    assert by_id(signals.compute(evidence, "http://t.test/"))["form_to_foreign_host"]["value"] is False


def login_posting_to(page, destination):
    evidence = copy.deepcopy(PAGE_B_EVIDENCE)
    evidence["destinations"] = [dest("form-action", "observed", page, destination, False)]
    return evidence


def test_form_to_a_subdomain_of_the_same_site_is_not_foreign():
    page = "http://northwind-bank.test/login-subdomain"
    found = signals.compute(login_posting_to(page, "http://auth.northwind-bank.test/session"), page)
    assert by_id(found)["form_to_foreign_host"]["value"] is False and signals.level(found) == signals.NOT_PROVEN


def test_form_to_another_host_of_the_same_customer_is_not_foreign():
    page = "http://northwind-verify.test/login"
    found = signals.compute(login_posting_to(page, "http://tailspin-offers.test/session"), page)
    assert signals.level(found) == signals.NOT_PROVEN


def test_form_to_a_different_customer_is_foreign():
    page = "http://northwind-verify.test/login"
    found = signals.compute(login_posting_to(page, "http://northwind-bank.test/session"), page)
    assert signals.level(found) == signals.PROVEN


def test_form_to_another_customer_on_a_shared_domain_is_foreign():
    page = "http://tailspin.sites-demo.test/login-shared"
    found = signals.compute(login_posting_to(page, "http://harborlane.sites-demo.test/session"), page)
    assert inventory.registrable_domain("tailspin.sites-demo.test") == "sites-demo.test"
    assert by_id(found)["form_to_foreign_host"]["value"] is True and signals.level(found) == signals.PROVEN


@pytest.mark.parametrize("page,destination,foreign", [
    ("tailspin.sites-demo.test", "harborlane.sites-demo.test", True),
    ("tailspin.sites-demo.test", "auth.harborlane.sites-demo.test", True),
    ("auth.tailspin.sites-demo.test", "harborlane.sites-demo.test", True),
    ("tailspin.sites-demo.test", "auth.tailspin.sites-demo.test", False),
    ("tailspin.sites-demo.test", "tailspin-offers.test", False),
    ("auth.example.co.uk", "www.example.co.uk", False),
    ("example.co.uk", "other.co.uk", True),
    ("alice.github.io", "bob.github.io", True),
    ("northwind-bank.test", "northwind-bank.test.evil.test", True),
    ("northwind-bank.test", "evil-northwind-bank.test", True),
    ("10.0.0.1", "10.0.0.2", True),
    ("10.0.0.1", "10.0.0.1", False),
    ("example.com", None, True),
    ("unlisted.test", "other-unlisted.test", True),
])
def test_is_foreign(page, destination, foreign):
    assert inventory.is_foreign(page, destination) is foreign


def test_owner_lookup_walks_up_but_never_matches_a_lookalike():
    assert inventory.owner_of("auth.northwind-bank.test")["id"] == "cust-1002"
    assert inventory.owner_of("northwind-bank.test.evil.test") is None
    assert inventory.owner_of("xnorthwind-bank.test") is None
    assert inventory.owner_of(None) is None


def test_reported_host_lookup_stays_exact():
    assert inventory.customer_for("auth.northwind-bank.test") is None


def test_final_host_differs_is_per_registrable_domain():
    evidence = {**copy.deepcopy(PAGE_C_EVIDENCE), "final_url": "http://www.northwind-bank.test/"}
    assert by_id(signals.compute(evidence, "http://northwind-bank.test/"))["final_host_differs"]["value"] is False
    evidence["final_url"] = "http://elsewhere.test/"
    assert by_id(signals.compute(evidence, "http://northwind-bank.test/"))["final_host_differs"]["value"] is True


@pytest.mark.parametrize("level,allowed", [
    (signals.NOT_PROVEN, ("Close",)),
    (signals.SUSPICIOUS, ("Warn", "Close")),
    (signals.PROVEN, ("Suspend", "Warn", "Close")),
    (None, ()),
])
def test_allowed_actions(level, allowed):
    assert policy.allowed_actions(level) == allowed
