"""Real trials of the documents role on the containment page, one setting at a time. Run in the app container:

  docker compose exec -T -e DATA_DIR=/data/trial app python - < eval/documents_trial.py
"""
import json
import time

from agent import client as llm
from agent import roles
from app import db, pipeline, policy, signals, visitor

URL = "http://northwind-verify.test/containment"
RUNS = 5
SETTINGS = [("low", 6000), ("low", 12000), ("minimal", 6000)]

db.init()
result = visitor.run_visit(URL, "trial")
visitor.save(result, db.case_dir(0))
evidence = result["evidence"]
found = signals.compute(evidence, URL)
level = signals.level(found)
ids = [s["id"] for s in found]
facts = pipeline.evidence_facts(evidence)
allowed = policy.allowed_actions(level)
customer = {"id": "cust-1001", "name": "Tailspin Offers Ltd"}

out = []
for effort, max_tokens in SETTINGS:
    llm.REASONING_EFFORT["documents"] = effort
    llm.MAX_TOKENS = max_tokens
    llm.VultrClient.max_tokens = max_tokens
    for run in range(RUNS):
        case_id = db.create_case(URL, "northwind-verify.test", "trial", customer)
        t0 = time.monotonic()
        pipeline.run_role(case_id, "documents",
                          roles.document_messages(level, allowed, customer["name"], found, facts),
                          lambda text: roles.check_documents(text, level, ids), retries=pipeline.DOCUMENT_RETRIES)
        attempts = [{"status": a["detail"]["status"], "problem": a["detail"]["problem"]}
                    for a in db.audit_log(case_id) if a["event"] == "model_output"]
        usage = db.case_usage(case_id)
        out.append({"effort": effort, "max_tokens": max_tokens, "run": run + 1, "attempts": attempts,
                    "final": attempts[-1]["status"], "wall_s": round(time.monotonic() - t0, 1),
                    "cost_usd": round(usage["cost_usd"], 6), "calls": usage["calls"]})
print(json.dumps({"level": level, "runs": out}, indent=1, ensure_ascii=False))
