"""The only way a model is called: checks the limits first, records tokens and cost after."""
import time

from app import db

COST_LIMIT_USD = 15.0
DAILY_LIMIT_USD = 2.0
MAX_CALLS_PER_CASE = 6
MAX_CALLS_PER_APPEAL = 2


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
    prompt_price = completion_price = worst_case = 0.0
    if client.live:
        try:
            prompt_price, completion_price = prices_for(client)
        except Exception as e:
            raise Refused(f"no price for {client.model}: {type(e).__name__}") from None
        worst_case = client.max_tokens * completion_price
    appeal = role.startswith("appeal_letter")  # each appeal has its own allowance, inside the cost limits
    usage_id, refusal = db.reserve_usage(case_id, role, client.model, worst_case,
                                         None if appeal else MAX_CALLS_PER_CASE, MAX_CALLS_PER_APPEAL if appeal else None,
                                         COST_LIMIT_USD if client.live else None, DAILY_LIMIT_USD if client.live else None)
    if refusal:
        raise Refused(refusal)

    t0 = time.monotonic()
    try:
        result = client.complete(messages, role)
    except Exception:
        db.finish_usage(usage_id, 0, 0, 0.0, round(time.monotonic() - t0, 3), ok=False)
        raise
    latency = round(time.monotonic() - t0, 3)
    cost = result["prompt_tokens"] * prompt_price + result["completion_tokens"] * completion_price
    db.finish_usage(usage_id, result["prompt_tokens"], result["completion_tokens"], cost, latency)
    return {"text": result["text"], "finish_reason": result.get("finish_reason"), "latency_s": latency,
            "cost_usd": cost}
