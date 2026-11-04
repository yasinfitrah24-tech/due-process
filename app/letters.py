"""What leaves the desk. The result sentence comes from code; the model writes only the rest.

Signal ids stay in the draft for the analyst and are removed from the version that is sent.
"""
import re

ACTION_TEXT = {
    "Suspend": "The service was suspended.",
    "Warn": "The customer was warned.",
    "Close": "The report was closed with no action.",
}
CITATION = re.compile(r"\s*\[[A-Za-z0-9_.-]+\]")


TEMPLATE_MODEL = "template, model unavailable"
TEMPLATE_FACTS = {
    "form_to_foreign_host": "A form on the page sends what is typed to another host.",
    "password_field": "The page has a password field.",
    "redirect_count": "The page sent the browser through one or more redirects.",
    "final_host_differs": "The visit ended on a different site from the one reported.",
    "hidden_text_present": "The page holds text that is hidden from visitors.",
    "egress_blocked": "The page tried to reach an address it may not reach.",
    "download_quarantined": "The page started a download, which was held and never opened.",
}


def template_documents(level, found):
    """Drafts without a model: one sentence per signal that is present. The result sentence is added on sending."""
    facts = [{"text": TEMPLATE_FACTS[s["id"]], "signals": [s["id"]], "factual": True} for s in found if s["value"]]
    facts = facts or [{"text": "Our review did not reproduce the reported behavior.", "signals": [found[0]["id"]],
                       "factual": True}]
    return {"statement_of_reasons": facts, "customer_notice": None if level == "Not proven" else facts,
            "reporter_reply": facts}


def outcome_sentence(level, action):
    return f"Result of our review: {level}. {ACTION_TEXT[action]}"


def sent_text(sentences, level, action):
    body = " ".join(CITATION.sub("", s["text"]).strip() for s in sentences)
    return f"{body}\n\n{outcome_sentence(level, action)}"
