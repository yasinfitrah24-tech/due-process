"""What leaves the desk: result sentence from code, no signal ids, both versions kept."""
import json
import re

import pytest

from agent import roles
from app import letters, policy
from tests.test_model_pipeline import (EVIDENCE, EXPECTED, URL_A, URL_B, URL_D, client, log, replies, run)  # noqa: F401

IDS = ["form_to_foreign_host", "password_field"]
LEVELS = ("Proven", "Suspicious", "Not proven")
ACTIONS = ("Suspend", "Warn", "Close")


def sentence(text, signals=("password_field",), factual=True):
    return {"text": text, "signals": list(signals), "factual": factual}


def draft(text):
    return json.dumps({"statement_of_reasons": [sentence(text)], "customer_notice": [sentence("Fine.")],
                       "reporter_reply": [sentence("Fine.")]})


def test_sent_text_has_no_citations_and_ends_with_the_result():
    text = letters.sent_text([sentence("A form posts elsewhere [form_to_foreign_host]."), sentence("Thanks.")],
                             "Proven", "Suspend")
    assert "[" not in text and "form_to_foreign_host" not in text
    assert text == "A form posts elsewhere. Thanks.\n\nResult of our review: Proven. The service was suspended."


@pytest.mark.parametrize("text", [
    "The page is proven to be abusive.", "This looks suspicious.", "We will suspend the service.", "Service suspended.",
    "We warned the customer.", "This is a warning.", "We closed the report.", "The account was terminated.",
    "The site is blocked.", "The page was taken down.",
])
def test_a_draft_may_not_state_a_result_or_action(text):
    with pytest.raises(roles.Rejected):
        roles.check_documents(draft(text), "Proven", IDS)


def test_a_draft_may_not_write_a_signal_id():
    with pytest.raises(roles.Rejected):
        roles.check_documents(draft("See password_field for this."), "Proven", IDS)


@pytest.mark.parametrize("url", [URL_A, URL_B, URL_D])
def test_letters_never_state_another_level_or_action(client, url):  # noqa: F811
    c = run(client, url)
    level, allowed = c["level"], policy.allowed_actions(c["level"])
    action = allowed[0]
    assert client.post(f"/api/cases/{c['id']}/actions", json={"action": action}).status_code == 200
    for name in ("statement_of_reasons", "customer_notice", "reporter_reply"):
        client.post(f"/cases/{c['id']}/documents/{name}/send")
    sent = client.get(f"/api/cases/{c['id']}").json()["sent"]
    assert "reporter_reply" in sent
    for text in sent.values():
        assert text.endswith(letters.outcome_sentence(level, action))
        assert text.count("Result of our review") == 1
        for other in LEVELS:
            found = re.findall(rf"(?<!Not ){other}", text) if other == "Proven" else re.findall(other, text)
            assert bool(found) == (other == level)
        for other in ACTIONS:
            assert (letters.ACTION_TEXT[other] in text) == (other == action)


def test_both_versions_are_kept(client):  # noqa: F811
    c = run(client, URL_A)
    client.post(f"/api/cases/{c['id']}/actions", json={"action": "Warn"})
    client.post(f"/cases/{c['id']}/documents/reporter_reply/send")
    c = client.get(f"/api/cases/{c['id']}").json()
    event = next(a for a in c["audit"] if a["event"] == "document_sent")
    assert event["detail"]["cited_draft"][0]["signals"]
    assert "[" not in event["detail"]["sent_text"] and "warned" in event["detail"]["sent_text"]
    page = client.get(f"/cases/{c['id']}").text
    assert "Sent (simulated) as:" in page and "<code>[" in page


def test_documents_are_retried_once_with_the_reason(client, replies, log):  # noqa: F811
    replies["documents"] = ["not json"]
    c = run(client, URL_A)
    calls = [e for e in log if e["role"] == "documents"]
    assert len(calls) == 2 and "rejected" in calls[1]["messages"][-1]["content"]
    assert c["outputs"]["documents"]["status"] == "accepted"
    assert [a["detail"]["status"] for a in c["audit"]
            if a["event"] == "model_output" and a["detail"]["role"] == "documents"] == ["rejected", "accepted"]


def test_empty_documents_are_retried_too(client, replies, log):  # noqa: F811
    replies["documents"] = [""]
    assert run(client, URL_A)["outputs"]["documents"]["status"] == "accepted"


def test_only_one_retry(client, replies, log):  # noqa: F811
    replies["documents"] = ["no", "no", "no"]
    c = run(client, URL_A)
    assert sum(e["role"] == "documents" for e in log) == 2
    assert c["outputs"]["documents"]["status"] == "rejected"


def test_other_roles_are_not_retried(client, replies, log):  # noqa: F811
    replies["verdict_claim"] = "not json"
    run(client, URL_A)
    assert sum(e["role"] == "verdict_claim" for e in log) == 1


def test_retry_stays_inside_the_case_limit(client, replies, log):  # noqa: F811
    replies["documents"] = ["no", "no"]
    c = run(client, URL_A)
    assert c["usage"]["calls"] <= 6
