"""Deletes every case and rebuilds one example per test page, with real visits and real model calls.

Run with `make reset-demo` or `make reset-demo-vm`. The cost ledger stays: what was spent was spent.
"""
import shutil

from app import db, inventory, pipeline
from app.examples import EXAMPLES


def main():
    db.init()
    removed = db.clear_cases()
    shutil.rmtree(db.data_dir() / "cases", ignore_errors=True)
    print(f"removed {removed} cases", flush=True)
    for key, example in EXAMPLES.items():
        host = inventory.reported_host(example["url"])
        case_id = db.create_case(example["url"], host, example["claim"], inventory.customer_for(host))
        db.audit(case_id, "demo", "report_received", url=example["url"], customer_id=db.get_case(case_id)["customer_id"])
        pipeline.process(case_id)
        print(f"case {case_id} {key}: {db.get_case(case_id)['level']}", flush=True)


if __name__ == "__main__":
    main()
