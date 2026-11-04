"""One case from report to verdict: plan, disposable visit, signals, level."""
import json

from agent import client as llm
from agent import planner
from app import db, signals
from sandbox import launch


def process(case_id):
    case = db.get_case(case_id)
    try:
        plan = planner.make_plan(llm.get_client(), case["host"], case["claim"], db.take_llm_call)
        db.update_case(case_id, status="visiting", plan=plan)
        db.audit(case_id, "planner", "plan_created", planner=plan["planner"], steps=plan["steps"],
                 problem=plan["problem"])

        result = launch.run_visit(case["url"], f"case-{case_id}", plan["steps"])
        launch.save(result, db.case_dir(case_id))
        receipt = result["receipt"]
        db.audit(case_id, "sandbox", "sandbox_visit", ok=result["ok"], container_id=receipt["container_id"],
                 container_gone=receipt["teardown"]["container_gone"])

        # No level above Not proven without evidence from a proven, torn-down sandbox.
        if result["ok"]:
            found = signals.compute(result["evidence"], case["url"])
            level, note = signals.level(found), None
        else:
            found, level, note = [], signals.NOT_PROVEN, "The sandbox visit did not complete; nothing was reproduced."
        db.update_case(case_id, status="ready", level=level, note=note, signals=found, receipt=receipt)
        db.audit(case_id, "system", "verdict_computed", level=level,
                 signals={s["id"]: s["value"] for s in found})
    except Exception as e:
        db.update_case(case_id, status="ready", level=signals.NOT_PROVEN, note=f"Processing failed: {type(e).__name__}")
        db.audit(case_id, "system", "processing_failed", error=type(e).__name__)


def load_evidence(case_id):
    path = db.case_dir(case_id) / "evidence.json"
    return json.loads(path.read_text()) if path.exists() else {}
