"""One case from report to verdict and drafts: disposable visit, signals, level, then model claims.

The level is stored before the first model call, so no model output can reach it.
"""
import json

from agent import client as llm
from agent import roles
from app import db, echo, inventory, ledger, letters, policy, signals, visitor


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
    """A model call, repeated up to `retries` times if it fails, with the reason. Whatever happens is kept, so a
    refused or rejected claim stays visible. Returns the last attempt."""
    for _ in range(retries + 1):
        entry = call_role(case_id, role, messages, check)
        if entry["status"] in ("accepted", "skipped"):
            break
        messages = messages + [{"role": "user", "content": f"Your reply was rejected: {entry['problem']}. "
                                                           "Reply again with corrected JSON only."}]
    return entry


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


def page_texts(evidence):
    """The page's words as the isolated world read them, visible and hidden."""
    return ([p["text"] for p in evidence.get("pages", []) if p.get("text")]
            + [h["text"] for h in evidence.get("hidden_text", [])])


def check_vision(text, evidence):
    claim = roles.check_vision(text)
    return {**claim, "repeats_page_text": echo.repeats(claim["description"], page_texts(evidence))}


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
        run_role(case_id, "vision_claim", roles.vision_messages(image), lambda text: check_vision(text, evidence))

    level, found = case["level"], case["signals"]
    ids = [s["id"] for s in found]
    allowed = policy.allowed_for(case)
    facts = evidence_facts(evidence)
    run_role(case_id, "verdict_claim", roles.verdict_messages(level, found, facts),
             lambda text: roles.check_verdict(text, level, ids))
    if case["mode"] == "demo":
        last = run_role(case_id, "documents",
                        roles.document_messages(level, allowed, case["customer_name"], found, facts),
                        lambda text: roles.check_documents(text, level, ids), retries=DOCUMENT_RETRIES)
        if last["status"] != "accepted":
            db.save_output(case_id, "documents", letters.TEMPLATE_MODEL, {
                "status": "template", "output": letters.template_documents(level, found), "raw": None,
                "problem": last["problem"], "unknown_citations": [], "latency_s": None, "cost_usd": None})
            db.audit(case_id, "system", "documents_template", reason=last["problem"])


def revisit(appeal_id):
    """The same fixed visit in a new container. It is the only thing that can open Reinstate."""
    appeal = db.get_appeal(appeal_id)
    case = db.get_case(appeal["case_id"])
    case_id = case["id"]
    db.update_appeal(appeal_id, status="visiting")
    found, level, note = [], None, "The second visit did not complete; nothing can be compared."
    try:
        result = visitor.run_visit(case["url"], f"case-{case_id}-appeal-{appeal_id}")
        visitor.save(result, db.case_dir(case_id) / f"appeal-{appeal_id}")
        receipt = result["receipt"]
        db.audit(case_id, "sandbox", "sandbox_revisit", appeal_id=appeal_id, ok=result["ok"],
                 container_id=receipt["container_id"], container_gone=receipt["teardown"]["container_gone"])
        db.update_appeal(appeal_id, receipt=receipt)
        # A page that did not load shows nothing, and that must not look like a clean page.
        if result["ok"] and not result["evidence"].get("errors"):
            found = signals.compute(result["evidence"], case["url"])
            level, note = signals.level(found), None
    except Exception as e:
        note = f"The second visit failed: {type(e).__name__}"
    db.update_appeal(appeal_id, status="drafting", level=level, note=note, signals=found)
    db.audit(case_id, "system", "revisit_computed", appeal_id=appeal_id, level=level, level_at_action=case["level"],
             signals={s["id"]: s["value"] for s in found})
    try:
        draft_appeal_letter(case, appeal_id, found)
    except Exception as e:
        db.audit(case_id, "system", "drafting_failed", appeal_id=appeal_id, error=type(e).__name__)
    db.update_appeal(appeal_id, status="ready")


def draft_appeal_letter(case, appeal_id, found):
    case_id, before, role = case["id"], case["signals"], f"appeal_letter:{appeal_id}"
    last = {"status": "skipped", "problem": "no second visit to compare"}
    if found:
        last = run_role(case_id, role, roles.appeal_messages(before, found, case["customer_name"]),
                        lambda text: roles.check_appeal_letter(text, before, found), retries=DOCUMENT_RETRIES)
    if last["status"] != "accepted":
        db.save_output(case_id, role, letters.TEMPLATE_MODEL, {
            "status": "template", "output": letters.template_appeal(before, found), "raw": None,
            "problem": last["problem"], "unknown_citations": [], "latency_s": None, "cost_usd": None})
        db.audit(case_id, "system", "appeal_letter_template", appeal_id=appeal_id, reason=last["problem"])


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
