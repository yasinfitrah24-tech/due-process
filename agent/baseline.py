"""Text-only triage: a model reads the report and decides, with no visit. A baseline, used to compare, never to act."""
import json
import re

PROMPT = """You triage abuse reports for a hosting provider. Decide from the report alone. Reply with one JSON object
and nothing else: {"action": "suspend|warn|close", "customer_id": "<customer id, or null if none>"}
The customers are: """


def messages(url, report, customers):
    listed = [{"id": c["id"], "name": c["name"], "hosts": c["hosts"]} for c in customers]
    return [{"role": "system", "content": PROMPT + json.dumps(listed)},
            {"role": "user", "content": f"Reported URL: {url}\nReport: {report}"}]


def parse(text):
    answer = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()))
    return {"action": str(answer["action"]).capitalize(), "target": answer.get("customer_id")}
