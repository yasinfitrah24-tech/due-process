"""One case from report to verdict and drafts: disposable visit, signals, level, then model claims.

The level is stored before the first model call, so no model output can reach it.
"""
import json

from agent import client as llm
from agent import roles
from app import db, inventory, ledger, policy, signals, visitor


DOCUMENT_RETRIES = 1


def visit_and_judge(case):
    """Returns the evidence, or None if the visit produced none to rely on."""
    case_id = case["id"]
    db.update_case(case_id, status="visiting")
    try:
        result = visitor.run_visit(case["url"], f"case-{case_id}")
        visitor.save(result, db.case_dir(case_id))
        receipt = result["receipt"]
        db.audit(case_id, "sandbox", "sandbox_visit", ok=result["ok"], container_id=receipt["container_id"],
                 container_gone=receipt["teardown"]["container_gone"])

        # No level above Not proven without evidence from a proven, torn-down sandbox.
        if result["ok"]:
            found = signals.compute(result["evidence"], case["url"])
            level, note = signals.level(found), None
        else:
            found, level, note = [], signals.NOT_PROVEN, "The sandbox visit did not complete; nothing was reproduced."
        db.update_case(case_id, status="drafting", level=level, note=note, signals=found, receipt=receipt)
        db.audit(case_id, "system", "verdict_computed", level=level,
                 signals={s["id"]: s["value"] for s in found})
        return result["evidence"] if result["ok"] else None
    except Exception as e:
        db.update_case(case_id, status="drafting", level=signals.NOT_PROVEN,
                       note=f"Processing failed: {type(e).__name__}")
        db.audit(case_id, "system", "processing_failed", error=type(e).__name__)
        return None


def run_role(case_id, role, messages, check, retries=0):
    """A model call, repeated up to `retries` times if its output is rejected, with the reason. Whatever happens is
    kept, so a refused or rejected claim stays visible."""
    for _ in range(retries + 1):
        entry = call_role(case_id, role, messages, check)
        if entry["status"] != "rejected":
            break
        messages = messages + [{"role": "user", "content": f"Your reply was rejected: {entry['problem']}. "
                                                           "Reply again with corrected JSON only."}]


def call_role(case_id, role, messages, check):
    client = llm.get_client(role)
    entry = {"status": "accepted", "output": None, "raw": None, "problem": None, "unknown_citations": [],
             "latency_s": None, "cost_usd": None}
    try:
        call = ledger.call(client, case_id, role, messages)
        entry.update(raw=call["text"], latency_s=call["latency_s"], cost_usd=call["cost_usd"])
        if not call["text"].strip():
            raise roles.Rejected(f"model returned no text (finish_reason: {call['finish_reason']})")
        entry["output"] = check(call["text"])
    except ledger.Refused as e:
        entry.update(status="skipped", problem=str(e))
    except roles.Rejected as e:
        entry.update(status="rejected", problem=e.reason, unknown_citations=e.unknown)
    except Exception as e:
        entry.update(status="failed", problem=type(e).__name__)
    db.save_output(case_id, role, client.model, entry)
    db.audit(case_id, "model", "model_output", role=role, model=client.model, status=entry["status"],
             problem=entry["problem"])
    return entry


def evidence_facts(evidence):
    """What verdict_claim and documents may read: evidence only, never the report text."""
    return {
        "final_url": evidence.get("final_url"),
        "redirects": [{"type": r["type"], "from": r["from"], "to": r["to"]} for r in evidence.get("redirects", [])],
        "form_destinations": [
            {"via": d["via"], "kind": d["kind"], "url": d["url"],
             "foreign": inventory.is_foreign(signals.host(d["page"]), signals.host(d["url"]))}
            for d in evidence.get("destinations", []) if "form_index" in d],
        "password_fields": len(evidence.get("password_fields", [])),
        "hidden_text": [h["text"] for h in evidence.get("hidden_text", [])],
    }


def last_screenshot(case_id, evidence):
    files = [p["screenshot"]["file"] for p in evidence.get("pages", []) if (p.get("screenshot") or {}).get("file")]
    return (db.case_dir(case_id) / files[-1]).read_bytes() if files else None


def draft(case_id, evidence):
    case = db.get_case(case_id)
    if case["mode"] == "demo":
        run_role(case_id, "claim_extraction", roles.claim_messages(case["claim"]), roles.check_claim)
    if evidence is None or not case["signals"]:
        return
    image = last_screenshot(case_id, evidence)
    if image:
        run_role(case_id, "vision_claim", roles.vision_messages(image), roles.check_vision)

    level, found = case["level"], case["signals"]
    ids = [s["id"] for s in found]
    allowed = policy.allowed_for(case)
    facts = evidence_facts(evidence)
    run_role(case_id, "verdict_claim", roles.verdict_messages(level, found, facts),
             lambda text: roles.check_verdict(text, level, ids))
    if case["mode"] == "demo":
        run_role(case_id, "documents", roles.document_messages(level, allowed, case["customer_name"], found, facts),
                 lambda text: roles.check_documents(text, level, ids), retries=DOCUMENT_RETRIES)


def process(case_id):
    evidence = visit_and_judge(db.get_case(case_id))
    try:
        draft(case_id, evidence)
    except Exception as e:
        db.audit(case_id, "system", "drafting_failed", error=type(e).__name__)
    db.update_case(case_id, status="ready")


def load_evidence(case_id):
    path = db.case_dir(case_id) / "evidence.json"
    return json.loads(path.read_text()) if path.exists() else {}
