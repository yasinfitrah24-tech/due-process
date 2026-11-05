import pytest

from app import ratelimit


@pytest.fixture(autouse=True)
def fresh_rate_limit():
    ratelimit.reset()
