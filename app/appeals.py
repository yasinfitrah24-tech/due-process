"""Customer appeals: a second visit in a new sandbox, then an analyst's decision.

What a customer writes is untrusted, like report text. It is stored and shown to the analyst, and it reaches no model and
no decision. Only the second visit's evidence can open Reinstate.
"""
import re

from app import db, policy

MAX_PER_CASE = 3
MAX_TEXT_CHARS = 2000
CHOICES = {"fixed": "I fixed it", "contested": "I contest this"}
PENDING = ("requested", "visiting", "drafting")
FIXABLE_HOST = "harborlane-shop.test"
FIXABLE_PATH = re.compile(r"/shop/([a-z0-9]{8,32})")


def closed_reason(case, appeals):
    """Why this case cannot be appealed now, or None."""
    if any(a["status"] in PENDING for a in appeals):
        return "An appeal is already being checked"
    if any(a["status"] == "ready" for a in appeals):
        return "An analyst is reviewing the last appeal"
    if any(a["decision"] == "Reinstate" for a in appeals):
        return "This case was reinstated"
    if len(appeals) >= MAX_PER_CASE:
        return f"This case has reached the limit of {MAX_PER_CASE} appeals"
    return None


def lower_level(case, appeal):
    return appeal["level"] is not None and "Reinstate" in policy.appeal_actions(case["level"], appeal["level"])


def allowed(case, appeal):
    return policy.appeal_actions(case["level"], appeal["level"]) if appeal["status"] == "ready" else ()


def comparison(case, appeal):
    """[{"id", "before", "after", "kind"}]: both visits, signal by signal."""
    after = {s["id"]: s for s in appeal["signals"] or []}
    return [{"id": s["id"], "before": s["value"], "after": after[s["id"]]["value"] if s["id"] in after else None,
             "kind": "observed"} for s in case["signals"] or []]


def fixable_slug(case):
    """The page's id if this case is on the fixable demo page, else None."""
    if case["host"] != FIXABLE_HOST:
        return None
    match = FIXABLE_PATH.fullmatch(case["url"].split(FIXABLE_HOST, 1)[1])
    return match.group(1) if match else None


def new_token_if_needed(case_id, action):
    if action in policy.APPEALABLE:
        db.set_appeal_token(case_id)
