"""LLM clients. Every real call goes to Vultr Serverless Inference (OpenAI-compatible); no other provider."""
import json
import os

import httpx

DEFAULT_BASE_URL = "https://api.vultrinference.com/v1"
TIMEOUT_S = 90
MAX_TOKENS = 6000
# Left at its default, reasoning on the documents role used up all of MAX_TOKENS and returned no text.
REASONING_EFFORT = {"documents": "low"}


def mock_reply(role, messages):
    if role == "claim_extraction":
        return {"category": "other", "alleged_brand": None, "alleged_behavior": "Mock extraction."}
    if role == "vision_claim":
        return {"description": "Mock description.", "imitated_brand": None}
    data = json.loads(messages[1]["content"])  # a retry adds messages after the data
    ids = [s["id"] for s in data["signals"] if s["value"]] or [data["signals"][0]["id"]]
    sentence = {"text": "Mock statement.", "signals": ids, "factual": True}
    if role == "verdict_claim":
        return {"level": data["level"], "sentences": [{"text": sentence["text"], "signals": ids}]}
    notice = None if data["level"] == "Not proven" else [sentence]
    return {"statement_of_reasons": [sentence], "customer_notice": notice, "reporter_reply": [sentence]}


class MockClient:
    """Used when the key or the model for a role is empty, and in tests. Makes no network call."""
    model = "mock"
    live = False
    max_tokens = 0

    def complete(self, messages, role):
        return {"text": json.dumps(mock_reply(role, messages)), "prompt_tokens": 0, "completion_tokens": 0}


class VultrClient:
    live = True
    max_tokens = MAX_TOKENS

    def __init__(self, api_key, model, base_url=DEFAULT_BASE_URL):
        self.api_key, self.model, self.base_url = api_key, model, base_url.rstrip("/")

    def headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    def complete(self, messages, role):
        effort = {"reasoning_effort": REASONING_EFFORT[role]} if role in REASONING_EFFORT else {}
        r = httpx.post(f"{self.base_url}/chat/completions", timeout=TIMEOUT_S, headers=self.headers(),
                       json={"model": self.model, "messages": messages, "temperature": 0,
                             "max_tokens": self.max_tokens, **effort})
        r.raise_for_status()
        body = r.json()
        usage = body.get("usage") or {}
        choice = body["choices"][0]
        return {"text": choice["message"].get("content") or "", "finish_reason": choice.get("finish_reason"),
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0)}

    def prices(self):
        """(prompt, completion) USD per token, from /v1/models."""
        r = httpx.get(f"{self.base_url}/models", timeout=TIMEOUT_S, headers=self.headers())
        r.raise_for_status()
        model = next(m for m in r.json()["data"] if m["id"] == self.model)
        text = next(i for i in model["input_modalities"] if i["type"] == "text")
        output = next(o for o in model["output_modalities"] if o["type"] == "text")
        prompt = next(p for p in text["pricing"] if p["type"] == "prompt")
        completion = next(p for p in output["pricing"] if p["type"] == "completion")
        return float(prompt["cost_usd"]), float(completion["cost_usd"])


def get_client(role):
    variable = "VULTR_VISION_MODEL" if role == "vision_claim" else "VULTR_MODEL"
    key, model = os.environ.get("VULTR_API_KEY", ""), os.environ.get(variable, "")
    if not key or not model:
        return MockClient()
    return VultrClient(key, model, os.environ.get("VULTR_BASE_URL") or DEFAULT_BASE_URL)
