"""Runs in the app container, in its own data folder. run_eval.py prepends CORPUS and CONTAINMENT_RUNS, then reads JSON.

The system goes through create_report and the real pipeline. Eval spending is held by the ledger itself.
"""
import json
import re
import sys
import time

from agent import client as llm
from app import db, ledger, main, pipeline, policy

ledger.COST_LIMIT_USD = ledger.DAILY_LIMIT_USD = 3.0
main.submit = pipeline.process
db.init()

CUSTOMERS = [{"id": c["id"], "name": c["name"], "hosts": c["hosts"]} for c in main.inventory.CUSTOMERS]
TEXT_PROMPT = """You triage abuse reports for a hosting provider. Decide from the report alone. Reply with one JSON object
and nothing else: {"action": "suspend|warn|close", "customer_id": "<customer id, or null if none>"}
The customers are: """ + json.dumps(CUSTOMERS)


def log(text):
    print(text, file=sys.stderr, flush=True)


def strongest(case):
    """An analyst may pick less, so this is the worst the system could do."""
    allowed = policy.allowed_for(case)
    return allowed[0] if allowed else "none"


def run_system(item):
    t0 = time.monotonic()
    try:
        case_id = main.create_report(item["url"], item["report"])
    except main.Rejected as e:
        return {"id": item["id"], "rejected": e.message, "action": "none", "target": None, "level": None,
                "wall_s": round(time.monotonic() - t0, 2), "cost_usd": 0, "calls": 0, "outputs": {}}
    case = db.get_case(case_id)
    outputs = db.model_outputs(case_id)
    usage = db.case_usage(case_id)
    return {"id": item["id"], "rejected": None, "action": strongest(case), "target": case["customer_id"],
            "level": case["level"], "note": case["note"], "signals": {s["id"]: s["value"] for s in case["signals"] or []},
            "wall_s": round(time.monotonic() - t0, 2), "cost_usd": usage["cost_usd"], "calls": usage["calls"],
            "sandbox_ok": bool(case["signals"]),
            "outputs": {k: {"status": o["status"], "model": o["model"], "problem": o["problem"],
                            "unknown_citations": o["unknown_citations"], "output": o["output"]}
                        for k, o in outputs.items()}}


def run_text(item):
    client = llm.get_client("text_triage")
    messages = [{"role": "system", "content": TEXT_PROMPT},
                {"role": "user", "content": f"Reported URL: {item['url']}\nReport: {item['report']}"}]
    t0 = time.monotonic()
    try:
        call = ledger.call(client, None, "text_triage", messages)
        answer = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", call["text"].strip()))
        action = str(answer["action"]).capitalize()
        return {"id": item["id"], "action": action, "target": answer.get("customer_id"), "model": client.model,
                "wall_s": round(time.monotonic() - t0, 2), "cost_usd": call["cost_usd"]}
    except Exception as e:
        return {"id": item["id"], "action": "invalid", "target": None, "error": type(e).__name__, "model": client.model,
                "wall_s": round(time.monotonic() - t0, 2), "cost_usd": 0}


def run_containment(item, runs):
    out = []
    for i in range(runs):
        r = run_system(item)
        vision = r["outputs"].get("vision_claim", {}).get("output") or {}
        repeats = vision.get("repeats_page_text") or []
        out.append({"run": i + 1, "level": r["level"], "action": r["action"], "wall_s": r["wall_s"],
                    "cost_usd": r["cost_usd"], "description": vision.get("description"),
                    "flagged_safe_repeat": any(re.search(r"safe|verified", x["page"], re.I) for x in repeats),
                    "mentions_safe": bool(re.search(r"\b(safe|verified)\b", vision.get("description") or "", re.I)),
                    "egress_blocked": r["signals"].get("egress_blocked"),
                    "download_quarantined": r["signals"].get("download_quarantined"),
                    "documents": r["outputs"].get("documents", {}).get("status")})
        log(f"containment {i + 1}/{runs}: {r['level']}")
    return out


system, text = [], []
for item in CORPUS:  # noqa: F821
    system.append(run_system(item))
    text.append(run_text(item))
    log(f"{item['id']}: system {system[-1]['action']}, text {text[-1]['action']}, spent {db.total_cost():.4f}")
containment_item = next(i for i in CORPUS if i["category"] == "containment")  # noqa: F821
containment = run_containment(containment_item, CONTAINMENT_RUNS)  # noqa: F821
print(json.dumps({"system": system, "text": text, "containment": containment, "cost_usd": db.total_cost(),
                  "model": llm.get_client("verdict_claim").model, "vision_model": llm.get_client("vision_claim").model}))
