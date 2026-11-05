"""What leaves the desk. The result sentence comes from code; the model writes only the rest.

Signal ids stay in the draft for the analyst and are removed from the version that is sent.
"""
import re

ACTION_TEXT = {
    "Suspend": "The service was suspended.",
    "Warn": "The customer was warned.",
    "Close": "The report was closed with no action.",
}
APPEAL_CLOSING = "If you have fixed this or believe it is wrong, use the appeal link below."
CITATION = re.compile(r"\s*\[[A-Za-z0-9_.-]+\]")
REQUEST = re.compile(r"(?:please|let us know)\b", re.I)


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


APPEAL_DECISION_TEXT = {"Reinstate": "The service was reinstated.", "Keep": "The action stays in place."}


def template_appeal(before, after):
    """A letter without a model: what changed between the two visits, per signal."""
    now = {s["id"]: s["value"] for s in after}
    sentences = []
    for s in before:
        if s["value"] and s["id"] in now:
            state = "This is still there" if now[s["id"]] else "This is gone now"
            sentences.append({"text": f"{TEMPLATE_FACTS[s['id']]} {state}.", "factual": True,
                              "signals": [f"before.{s['id']}", f"after.{s['id']}"]})
    return {"sentences": sentences or [{"text": "We could not compare the two visits.", "factual": False, "signals": []}]}


def appeal_sent_text(sentences, level, decision):
    result = f"Result of the second visit: {level}." if level else "The second visit could not be completed."
    return f"{clean(sentences)}\n\n{result} {APPEAL_DECISION_TEXT[decision]}"


def clean(sentences):
    return " ".join(CITATION.sub("", s["text"]).strip() for s in sentences)


def outcome_sentence(level, action):
    return f"Result of our review: {level}. {ACTION_TEXT[action]}"


def is_request(sentence):
    return not sentence.get("factual", True) and REQUEST.match(sentence["text"])


def sent_text(sentences, level, action, appeal_url=None, why=None):
    """At Not proven the draft may name no absent signal, so code says what cleared the page. The reader then gets the
    reason, the result, and last what is asked of them."""
    if level == "Not proven":
        requests = [s for s in sentences if is_request(s)]
        parts = [clean([s for s in sentences if not is_request(s)]), why, outcome_sentence(level, action), clean(requests)]
        return "\n\n".join(p for p in parts if p)
    text = f"{clean(sentences)}\n\n{outcome_sentence(level, action)}"
    return f"{text}\n\n{APPEAL_CLOSING}\n{appeal_url}" if appeal_url else text
