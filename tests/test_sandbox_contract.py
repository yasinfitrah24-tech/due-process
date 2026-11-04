"""The docker flags and URL handling, without running Docker."""
import json
import subprocess
from datetime import datetime, timezone

import pytest

from sandbox import launch


def flags():
    return launch.docker_args("dp-case-x", "cid")


def has_pair(args, key, value):
    return any(a == key and b == value for a, b in zip(args, args[1:]))


@pytest.mark.parametrize("key,value", [
    ("--user", "pwuser"),
    ("--tmpfs", "/tmp:rw,size=256m"),
    ("--cap-drop", "ALL"),
    ("--security-opt", "no-new-privileges"),
    ("--memory", "1g"),
    ("--memory-swap", "1g"),
    ("--pids-limit", "256"),
    ("--shm-size", "512m"),
    ("--cpus", "1"),
    ("--network", "dp-sandbox"),
])
def test_contract_flag_present(key, value):
    assert has_pair(flags(), key, value)


def test_read_only_and_removed_on_exit():
    assert "--read-only" in flags() and "--rm" in flags()


def test_seccomp_profile_is_the_chroot_one_and_exists():
    assert launch.SECCOMP.name == "playwright-v1.64.1-chroot.json" and launch.SECCOMP.is_file()
    assert has_pair(flags(), "--security-opt", f"seccomp={launch.SECCOMP}")


@pytest.mark.parametrize("forbidden", ["-e", "--env", "--env-file", "--privileged", "--cap-add", "-v", "--volume",
                                       "--mount", "--network=host"])
def test_nothing_from_the_host_goes_in(forbidden):
    assert forbidden not in flags()


def test_chromium_sandbox_stays_on():
    assert launch.visit_args("http://x.test/")[:2] == ["--sandbox", "on"]


def test_chromium_goes_through_the_proxy():
    args = launch.visit_args("http://x.test/")
    assert args[args.index("--proxy") + 1] == "http://egress:3128"


def test_url_cannot_become_an_option():
    args = launch.visit_args("--sandbox=off")
    assert args[-2:] == ["--", "--sandbox=off"]


@pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:alert(1)", "--sandbox=off", "http://", "chrome://version"])
def test_only_http_urls_are_visited(url):
    with pytest.raises(ValueError):
        launch.run_visit(url)


def test_visit_budget_ends_before_the_hard_kill():
    assert launch.VISIT_BUDGET_S + 5 < launch.HARD_TIMEOUT_S


def test_proxy_log_counts_only_this_visit(monkeypatch):
    lines = [{"ts": 1000.0, "client": "172.20.0.3", "host": "earlier-container.test"},
             {"ts": 2000.0, "client": "172.20.0.4", "host": "other-container.test"},
             {"ts": 2000.0, "client": "172.20.0.3", "host": "this-visit.test"}]
    out = "\n".join([json.dumps(x) for x in lines] + ["not json"])
    monkeypatch.setattr(launch.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out))
    started = datetime.fromtimestamp(1500, timezone.utc).isoformat()
    assert [e["host"] for e in launch.egress_log("172.20.0.3", "since", started)] == ["this-visit.test"]


def test_unreadable_proxy_log_is_none(monkeypatch):
    monkeypatch.setattr(launch.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout=""))
    assert launch.egress_log("172.20.0.3", "since", "2026-11-04T00:00:00+00:00") is None


def test_not_ok_without_namespace_proof():
    assert launch.namespace_proof(None)["in_new_user_pid_net_ns"] == 0
