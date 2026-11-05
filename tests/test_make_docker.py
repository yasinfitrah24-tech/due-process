"""`make test-docker` must fail loudly: a green exit with Docker off would read as tests that passed."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")


def make(docker, python):
    return subprocess.run(["make", "test-docker", f"DOCKER={docker}", f"PY={python}"], cwd=ROOT, capture_output=True,
                          text=True, timeout=60)


def test_docker_off_exits_non_zero_before_any_test_runs():
    r = make("false", "true")
    assert r.returncode != 0 and "Docker is not running" in r.stderr
    assert "pytest" not in r.stdout


def test_a_failing_test_run_exits_non_zero():
    assert make("true", "false").returncode != 0


def test_a_passing_run_exits_zero():
    assert make("true", "true").returncode == 0
