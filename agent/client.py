"""LLM clients. Every real call goes to Vultr Serverless Inference (OpenAI-compatible); no other provider."""
import json
import os

import httpx

DEFAULT_BASE_URL = "https://api.vultrinference.com/v1"
TIMEOUT_S = 30


class MockClient:
    """Used when VULTR_API_KEY is empty, and in tests. Makes no network call."""
    mode = "mock"
    live = False

    def complete(self, messages):
        return json.dumps({"steps": [{"action": "open"}, {"action": "follow_redirects"}, {"action": "screenshot"}],
                           "rationale": "Mock planner: standard visit."})


class VultrClient:
    live = True

    def __init__(self, api_key, model, base_url=DEFAULT_BASE_URL):
        self.api_key, self.model, self.base_url = api_key, model, base_url.rstrip("/")
        self.mode = f"vultr:{model}"

    def complete(self, messages):
        r = httpx.post(f"{self.base_url}/chat/completions", timeout=TIMEOUT_S,
                       headers={"Authorization": f"Bearer {self.api_key}"},
                       json={"model": self.model, "messages": messages, "temperature": 0})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


def get_client():
    key, model = os.environ.get("VULTR_API_KEY", ""), os.environ.get("VULTR_MODEL", "")
    if not key or not model:
        return MockClient()
    return VultrClient(key, model, os.environ.get("VULTR_BASE_URL") or DEFAULT_BASE_URL)
