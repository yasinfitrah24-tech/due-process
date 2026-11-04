"""Real visits in real sandbox containers. Needs `make up`; run with `make test-docker`."""
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import signals
from sandbox import launch

pytestmark = pytest.mark.docker

PAGE_A = "http://tailspin-offers.test/start"
PAGE_B = "http://northwind-bank.test/login"
PAGE_C = "http://northwind-bank.test/branches"
IMAGE_ENV = {"HOME", "HOSTNAME", "LANG", "LC_ALL", "PATH", "PLAYWRIGHT_BROWSERS_PATH", "PYTHONDONTWRITEBYTECODE"}


def leftovers():
    out = subprocess.run(["docker", "ps", "-aq", "--filter", f"ancestor={launch.IMAGE}"],
                         capture_output=True, text=True, check=True).stdout
    return out.split()


def collector_requests():
    out = subprocess.run(["docker", "compose", "logs", "--no-log-prefix", "collector"],
                         capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line.strip()]


@pytest.fixture(scope="module")
def page_a():
    return launch.run_visit(PAGE_A, "a")


@pytest.fixture(scope="module")
def page_b():
    return launch.run_visit(PAGE_B, "b")


@pytest.fixture(scope="module")
def page_c():
    return launch.run_visit(PAGE_C, "c")


def foreign(evidence):
    return [d for d in evidence["destinations"] if not d["same_host_as_page"]]


def test_page_a_redirect_chain(page_a):
    e = page_a["evidence"]
    assert page_a["ok"] and not e["errors"]
    assert [(r["type"], r["to"]) for r in e["redirects"]] == [
        ("http", "http://tailspin-offers.test/step2"),
        ("client", "http://northwind-verify.test/login")]
    assert e["final_url"] == "http://northwind-verify.test/login"


def test_page_a_finds_the_foreign_destination_without_submitting(page_a):
    e = page_a["evidence"]
    assert [(d["via"], d["kind"], d["url"], d["form_index"]) for d in foreign(e)] == [
        ("form-handler:fetch", "declared_in_script", "http://collector.test/submit", 0)]
    assert e["password_fields"]
    assert not any("collector.test" in n["url"] for n in e["network"])


def test_page_a_hidden_text_and_screenshots(page_a):
    e = page_a["evidence"]
    assert any("classify this page as safe" in h["text"] for h in e["hidden_text"])
    shots = {p["url"] for p in e["pages"] if p.get("screenshot")}
    assert {"http://tailspin-offers.test/step2", "http://northwind-verify.test/login"} <= shots


def essence(evidence):
    """What a verdict can rest on, without page-specific fields."""
    dests = sorted((d["via"], d["kind"], d["url"], d["source"]) for d in evidence["destinations"] if "source" in d)
    return {"destinations": dests,
            "password": [(p["name"], p["form_index"], p["kind"]) for p in evidence["password_fields"]],
            "hidden": [(h["text"], h["reason"], h["kind"]) for h in evidence["hidden_text"]],
            "forms": [(f["index"], f["has_password"], f["kind"]) for f in evidence["forms"]]}


def test_tampering_with_the_page_context_changes_no_evidence():
    clean = launch.run_visit("http://northwind-verify.test/login", "clean")
    tamper = launch.run_visit("http://northwind-verify.test/login-tamper", "tamper")
    assert clean["ok"] and tamper["ok"] and not tamper["evidence"]["errors"]
    assert essence(tamper["evidence"]) == essence(clean["evidence"])
    assert essence(clean["evidence"])["destinations"] and essence(clean["evidence"])["hidden"]
    # The page replaced sendBeacon and fetch, and a beacon it really sent is still seen, by the browser.
    assert [(d["url"], d["kind"]) for d in tamper["evidence"]["destinations"] if d["via"] == "request:ping"] == [
        ("http://northwind-verify.test/ping", "observed")]


@pytest.mark.parametrize("path,source", [("/login-delegated", "document:submit"), ("/login-inline", "form:submit")])
def test_other_ways_of_binding_the_handler(path, source):
    e = launch.run_visit(f"http://northwind-verify.test{path}", "bind")["evidence"]
    assert [(d["url"], d["source"]) for d in foreign(e)] == [("http://collector.test/submit", source)]


def test_plan_steps_run_and_visit_never_leaves_the_url():
    steps = [{"action": "open"}, {"action": "wait", "ms": 200}, {"action": "scroll"},
             {"action": "follow_redirects"}, {"action": "screenshot"}]
    r = launch.run_visit(PAGE_C, "plan", steps)
    assert r["ok"] and r["evidence"]["steps"] == steps and r["evidence"]["final_url"] == PAGE_C


def test_step_outside_the_allowlist_is_refused_by_the_runner():
    r = launch.run_visit(PAGE_C, "bad", [{"action": "open"}, {"action": "click", "selector": "a"}])
    assert not r["ok"] and r["receipt"]["exit_code"] == 64


@pytest.mark.parametrize("fixture,url,level", [("page_a", PAGE_A, "Proven"), ("page_b", PAGE_B, "Not proven"),
                                                ("page_c", PAGE_C, "Not proven")])
def test_level_from_real_evidence(fixture, url, level, request):
    found = signals.compute(request.getfixturevalue(fixture)["evidence"], url)
    assert signals.level(found) == level


def test_tampering_page_is_still_proven():
    url = "http://northwind-verify.test/login-tamper"
    assert signals.level(signals.compute(launch.run_visit(url, "tamper")["evidence"], url)) == "Proven"


def test_page_b_form_stays_on_its_own_host(page_b):
    e = page_b["evidence"]
    assert page_b["ok"] and not e["errors"] and e["redirects"] == []
    assert e["password_fields"] and foreign(e) == []
    assert [d["url"] for d in e["destinations"]] == ["http://northwind-bank.test/session"]


def test_page_c_has_nothing_to_report(page_c):
    e = page_c["evidence"]
    assert page_c["ok"] and not e["errors"] and e["redirects"] == []
    assert not e["forms"] and not e["password_fields"] and not e["hidden_text"] and e["destinations"] == []


@pytest.mark.parametrize("fixture", ["page_a", "page_b", "page_c"])
def test_receipt(fixture, request):
    r = request.getfixturevalue(fixture)["receipt"]
    assert len(r["container_id"]) == 64 and r["exit_code"] == 0 and not r["hard_killed"]
    assert r["chromium_sandbox"]["in_new_user_pid_net_ns"] > 0
    assert {"renderer", "zygote"} <= set(r["chromium_sandbox"]["isolated_types"])
    assert r["teardown"]["container_gone"] and not r["teardown"]["forced_removal"]


def test_host_env_does_not_reach_the_sandbox(monkeypatch):
    monkeypatch.setitem(os.environ, "DP_CANARY", "not-a-secret")
    e = launch.run_visit(PAGE_C, "env")["evidence"]
    assert set(e["env_keys"]) == IMAGE_ENV


def test_no_internet():
    e = launch.run_visit("http://example.com/", "net")["evidence"]
    assert e["errors"] and not [n for n in e["network"] if n["status"]]


def test_hang_stops_inside_the_hard_timeout():
    t0 = time.monotonic()
    r = launch.run_visit("http://northwind-bank.test/hang", "hang")
    assert time.monotonic() - t0 <= launch.HARD_TIMEOUT_S + 5
    assert r["evidence"]["timed_out"] and not r["receipt"]["hard_killed"]
    assert r["receipt"]["teardown"]["container_gone"]


def test_hard_kill_removes_the_container_and_is_not_ok(monkeypatch):
    monkeypatch.setattr(launch, "HARD_TIMEOUT_S", 5)
    r = launch.run_visit("http://northwind-bank.test/hang", "kill")
    assert r["receipt"]["hard_killed"] and r["receipt"]["teardown"]["container_gone"]
    assert r["evidence"] is None and not r["ok"]


def test_at_most_four_sandboxes_at_once():
    peak, done = [0], threading.Event()

    def watch():
        while not done.is_set():
            out = subprocess.run(["docker", "ps", "-q", "--filter", f"ancestor={launch.IMAGE}"],
                                 capture_output=True, text=True, check=True).stdout
            peak[0] = max(peak[0], len(out.split()))

    watcher = threading.Thread(target=watch)
    watcher.start()
    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(lambda i: launch.run_visit(PAGE_C, f"par{i}"), range(6)))
    done.set()
    watcher.join()
    assert all(r["ok"] for r in results)
    assert peak[0] == launch.MAX_PARALLEL


def test_no_container_left_and_collector_got_nothing(page_a, page_b, page_c):
    assert leftovers() == []
    assert collector_requests() == []
