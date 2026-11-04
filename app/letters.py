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


def outcome_sentence(level, action):
    return f"Result of our review: {level}. {ACTION_TEXT[action]}"


def sent_text(sentences, level, action):
    body = " ".join(CITATION.sub("", s["text"]).strip() for s in sentences)
    return f"{body}\n\n{outcome_sentence(level, action)}"
