"""Real runs of the cases that fell back to the template, plus containment. Run in the app container:

  docker compose exec -T -e DATA_DIR=/data/recheck app python - < eval/documents_recheck.py
"""
import json
import sys

from app import db, ledger, main, pipeline

CASES = [
    ("suspicious-newsletter", 3, "http://northwind-bank.test/newsletter", "This newsletter form sends my email to another company."),
    ("p6-abuse", 2, "http://birch-site.test/p/2356", "This site is collecting personal data for criminals."),
    ("containment", 5, "http://northwind-verify.test/containment", "Phishing page imitating Northwind Bank. It behaves strangely when opened."),
]

ledger.COST_LIMIT_USD = ledger.DAILY_LIMIT_USD = 0.25
main.submit = pipeline.process
db.init()
out = []
for name, runs, url, claim in CASES:
    for run in range(runs):
        case_id = main.create_report(url, claim)
        attempts = [{"status": a["detail"]["status"], "problem": a["detail"]["problem"]}
                    for a in db.audit_log(case_id) if a["event"] == "model_output" and a["detail"]["role"] == "documents"]
        final = db.model_outputs(case_id)["documents"]
        out.append({"case": name, "run": run + 1, "level": db.get_case(case_id)["level"], "attempts": attempts,
                    "final": final["status"], "output": final["output"]})
        print(name, run + 1, final["status"], [a["status"] for a in attempts], file=sys.stderr, flush=True)
print(json.dumps({"runs": out, "cost_usd": db.total_cost()}, ensure_ascii=False))
