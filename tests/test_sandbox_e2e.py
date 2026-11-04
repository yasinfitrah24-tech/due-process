"""Real visits in real sandbox containers. Needs `make up`; run with `make test-docker`."""
import hashlib
import json
import os
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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


@pytest.mark.parametrize("fixture,url,level", [("page_a", PAGE_A, "Proven"), ("page_b", PAGE_B, "Not proven"),
                                                ("page_c", PAGE_C, "Not proven")])
def test_level_from_real_evidence(fixture, url, level, request):
    found = signals.compute(request.getfixturevalue(fixture)["evidence"], url)
    assert signals.level(found) == level


def test_login_on_a_subdomain_of_the_same_site_is_not_proven():
    url = "http://northwind-bank.test/login-subdomain"
    e = launch.run_visit(url, "sub")["evidence"]
    assert [d["url"] for d in foreign(e)] == ["http://auth.northwind-bank.test/session"] and e["password_fields"]
    assert signals.level(signals.compute(e, url)) == "Not proven"


def test_login_posting_to_another_customer_on_a_shared_domain_is_proven():
    url = "http://tailspin.sites-demo.test/login-shared"
    e = launch.run_visit(url, "shared")["evidence"]
    assert signals.level(signals.compute(e, url)) == "Proven"


def test_page_d_is_suspicious():
    url = "http://tailspin-offers.test/verify-account"
    e = launch.run_visit(url, "d")["evidence"]
    assert [d["url"] for d in foreign(e)] == ["http://collector.test/verify"] and not e["password_fields"]
    assert signals.level(signals.compute(e, url)) == "Suspicious"


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


def compose_exec(service, command):
    return subprocess.run(["docker", "compose", "exec", "-T", service, "sh", "-c", command],
                          capture_output=True, text=True)


@pytest.fixture(scope="module")
def netcheck(tmp_path_factory):
    """Run inside a real sandbox container with the contract flags."""
    args = launch.docker_args(f"dp-case-net-{uuid.uuid4().hex[:8]}", tmp_path_factory.mktemp("c") / "cid")
    args[-1:-1] = ["-i", "--entrypoint", "python3"]
    script = Path(__file__).with_name("sandbox_netcheck.py").read_text()
    r = subprocess.run(args + ["-"], input=script, capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_from_the_sandbox_only_the_proxy_is_reachable(netcheck):
    direct = netcheck["direct"]
    assert direct.pop("egress:3128") == "connected"
    assert direct and "connected" not in direct.values()


@pytest.mark.parametrize("name", ["metadata", "metadata_decimal", "metadata_connect", "private_10", "private_192",
                                  "loopback", "launcher", "launcher_port80", "app", "docker_host", "fix_port"])
def test_from_the_sandbox_the_proxy_blocks(netcheck, name):
    assert netcheck["proxy"][name] == 403


def test_from_the_sandbox_the_proxy_passes_a_demo_host(netcheck):
    assert netcheck["proxy"]["demo_host"] == 302


def test_every_browser_request_is_in_the_proxy_log(page_a):
    e = page_a["evidence"]
    logged = {(x["host"], x["path"]) for x in e["egress"] if x["decision"] == "allowed"}
    assert ("tailspin-offers.test", "/start") in logged and ("northwind-verify.test", "/login") in logged
    assert page_a["receipt"]["egress_requests"] == len(e["egress"]) > 0


@pytest.fixture(scope="module")
def containment():
    return launch.run_visit("http://northwind-verify.test/containment", "containment")


def test_containment_metadata_is_blocked_and_recorded(containment):
    blocked = [x for x in containment["evidence"]["egress"] if x["decision"] == "blocked"]
    assert [(x["host"], x["reason"]) for x in blocked] == [("169.254.169.254", "cloud metadata address")]
    assert blocked[0]["kind"] == "observed"


def test_containment_download_stays_in_the_container(containment):
    e, r = containment["evidence"], containment["receipt"]
    sha = hashlib.sha256(b"TEST PAGE sample download. Fictional brand, harmless text.\n").hexdigest()
    assert [(d["suggested_filename"], d["sha256"]) for d in e["downloads"]] == [("northwind-statement.txt", sha)]
    assert r["quarantined_downloads"][0]["sha256"] == sha
    assert "harmless text" not in json.dumps({k: v for k, v in e.items() if k != "pages"})
    assert r["teardown"]["container_gone"] and containment["ok"]


def test_containment_does_not_move_the_level(containment, page_a):
    url = "http://northwind-verify.test/containment"
    found = signals.compute(containment["evidence"], url)
    values = {s["id"]: s["value"] for s in found}
    assert values["egress_blocked"] and values["download_quarantined"]
    assert signals.level(found) == "Proven"
    assert signals.level(signals.compute(page_a["evidence"], PAGE_A)) == "Proven"


def test_fixable_page_changes_only_for_its_own_id():
    fixed, other = f"http://harborlane-shop.test/shop/{uuid.uuid4().hex[:12]}", f"http://harborlane-shop.test/shop/{uuid.uuid4().hex[:12]}"
    before = launch.run_visit(fixed, "fix-before")["evidence"]
    assert signals.level(signals.compute(before, fixed)) == "Proven"
    slug = fixed.rsplit("/", 1)[1]
    script = f"import httpx; print(httpx.post('http://testsite:8081/fix/{slug}', timeout=10).status_code)"
    r = subprocess.run(["docker", "compose", "exec", "-T", "app", "python", "-c", script], capture_output=True, text=True)
    assert r.stdout.strip() == "204", r.stderr
    after = launch.run_visit(fixed, "fix-after")["evidence"]
    assert signals.level(signals.compute(after, fixed)) == "Not proven" and after["password_fields"]
    assert signals.level(signals.compute(launch.run_visit(other, "fix-other")["evidence"], other)) == "Proven"


def test_fix_port_changes_nothing_else():
    script = "import httpx; print([httpx.post('http://testsite:8081' + p, timeout=10).status_code for p in ('/fix/UPPER', '/fix/../x', '/login', '/')])"
    r = subprocess.run(["docker", "compose", "exec", "-T", "app", "python", "-c", script], capture_output=True, text=True)
    assert r.stdout.strip() == "[404, 404, 404, 404]", r.stdout + r.stderr


def test_events_arrive_while_the_visit_runs():
    seen = []
    t0 = time.monotonic()
    result = launch.run_visit("http://northwind-verify.test/containment", "events",
                              lambda e: seen.append({**e, "arrived": time.monotonic() - t0}))
    total = time.monotonic() - t0
    kinds = [e["kind"] for e in seen]
    for kind in ("sandbox_requested", "container_started", "visit_started", "navigation", "page_loaded", "download",
                 "egress_allowed", "egress_blocked", "visit_finished", "container_gone"):
        assert kind in kinds, kinds
    assert kinds[0] == "sandbox_requested" and kinds[-1] == "container_gone"
    first_blocked = next(e for e in seen if e["kind"] == "egress_blocked")
    assert first_blocked["host"] == "169.254.169.254" and first_blocked["arrived"] < total - 1
    assert next(e for e in seen if e["kind"] == "navigation")["arrived"] < total - 1
    assert result["ok"]


def test_app_has_no_docker():
    r = compose_exec("app", "test -e /var/run/docker.sock; echo socket=$?; command -v docker; echo client=$?")
    assert "socket=1" in r.stdout and "client=1" in r.stdout


def test_app_cannot_reach_docker_or_the_sandbox_networks():
    script = """
import socket
for host, port in [('egress', 3128), ('tailspin-offers.test', 80), ('collector.test', 80),
                   ('host.docker.internal', 2375), ('host.docker.internal', 2376)]:
    try:
        socket.create_connection((host, port), timeout=3).close()
        print('REACHED', host, port)
    except OSError:
        pass
print('done')
"""
    r = subprocess.run(["docker", "compose", "exec", "-T", "app", "python", "-c", script],
                       capture_output=True, text=True)
    assert r.stdout.strip() == "done", r.stdout + r.stderr


def launcher_call(method, path, body=None):
    script = f"""
import httpx
r = httpx.request({method!r}, 'http://launcher:8100{path}', json={body!r}, timeout=90)
print(r.status_code)
"""
    r = subprocess.run(["docker", "compose", "exec", "-T", "app", "python", "-c", script],
                       capture_output=True, text=True)
    return int(r.stdout.strip())


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/containers/json", None),
    ("POST", "/containers/create", {"Image": "alpine"}),
    ("POST", "/run", {"image": "alpine"}),
    ("POST", "/visit", {"url": "file:///etc/passwd"}),
    ("POST", "/visit", {"url": "--sandbox=off"}),
    ("POST", "/visit", {"case": "x"}),
])
def test_launcher_offers_visit_and_nothing_else(method, path, body):
    assert launcher_call(method, path, body) in (400, 404)


def test_extra_fields_cannot_change_the_flags():
    assert launcher_call("POST", "/visit", {"url": PAGE_C, "image": "alpine", "flags": ["--privileged"]}) == 200
    assert leftovers() == []


def test_only_the_launcher_holds_the_socket():
    out = subprocess.run(["docker", "compose", "ps", "-q"], capture_output=True, text=True).stdout.split()
    holders = []
    for cid in out:
        mounts = subprocess.run(["docker", "inspect", "-f", "{{.Name}} {{json .Mounts}}", cid],
                                capture_output=True, text=True).stdout
        if "docker.sock" in mounts:
            holders.append(mounts.split()[0])
    assert holders == ["/due-process-launcher-1"]


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
