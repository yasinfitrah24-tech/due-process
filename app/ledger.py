"""The only way a model is called: checks the limits first, records tokens and cost after."""
import time

from app import db

COST_LIMIT_USD = 5.0
MAX_CALLS_PER_CASE = 6


class Refused(Exception):
    pass


def prices_for(client):
    """(prompt, completion) USD per token, kept in the DB once read from /v1/models."""
    known = db.get_price(client.model)
    if known:
        return known
    prompt, completion = client.prices()
    db.save_price(client.model, prompt, completion)
    return prompt, completion


def call(client, case_id, role, messages):
    """Returns {"text", "finish_reason", "latency_s", "cost_usd"}. Refuses without calling if a limit would be broken or the price
    is unknown, because a call that cannot be priced cannot be held to the limit."""
    if db.case_calls(case_id) >= MAX_CALLS_PER_CASE:
        raise Refused(f"case limit of {MAX_CALLS_PER_CASE} model calls reached")
    prompt_price = completion_price = 0.0
    if client.live:
        try:
            prompt_price, completion_price = prices_for(client)
        except Exception as e:
            raise Refused(f"no price for {client.model}: {type(e).__name__}") from None
        if db.total_cost() + client.max_tokens * completion_price > COST_LIMIT_USD:
            raise Refused(f"cost limit of USD {COST_LIMIT_USD} reached")

    t0 = time.monotonic()
    try:
        result = client.complete(messages, role)
    except Exception:
        db.record_usage(case_id, role, client.model, 0, 0, 0.0, round(time.monotonic() - t0, 3), ok=False)
        raise
    latency = round(time.monotonic() - t0, 3)
    cost = result["prompt_tokens"] * prompt_price + result["completion_tokens"] * completion_price
    db.record_usage(case_id, role, client.model, result["prompt_tokens"], result["completion_tokens"], cost, latency)
    return {"text": result["text"], "finish_reason": result.get("finish_reason"), "latency_s": latency,
            "cost_usd": cost}
