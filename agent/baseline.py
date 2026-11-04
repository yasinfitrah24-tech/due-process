"""Text-only triage: a model reads the report and decides, with no visit. A baseline, used to compare, never to act."""
import json
import re

PROMPT = """You triage abuse reports for a hosting provider. Decide from the report alone. Reply with one JSON object
and nothing else: {"action": "suspend|warn|close", "customer_id": "<customer id, or null if none>"}
The customers are: """


def messages(url, report, customers, explain=False):
    """The eval uses this as it is. The comparison screen also asks for a short reason, to show next to the decision."""
    listed = [{"id": c["id"], "name": c["name"], "hosts": c["hosts"]} for c in customers]
    prompt = PROMPT.replace('"customer_id": "<customer id, or null if none>"}',
                            '"customer_id": "<customer id, or null if none>", "reason": "<one short sentence>"}') if explain else PROMPT
    return [{"role": "system", "content": prompt + json.dumps(listed)},
            {"role": "user", "content": f"Reported URL: {url}\nReport: {report}"}]


def parse(text):
    answer = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()))
    return {"action": str(answer["action"]).capitalize(), "target": answer.get("customer_id"),
            "reason": str(answer.get("reason") or "")[:300]}
