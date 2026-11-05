"""SCREENSHOT ONLY. Never run this on real data, and nothing here is used by the app or deployed in its image.

Makes the states that have never happened on a real system, in a throwaway copy of a data directory, so that
docs/screens/ can show them. The folder name must contain "screenshot-copy", or this refuses to touch it.

  python scripts/screenshot_only_stage.py prepare <copy>     before starting the app on the copy
  python scripts/screenshot_only_stage.py running <copy>     after it started: startup clears cases left running
  python scripts/screenshot_only_stage.py queue-full <copy>  for the 503 page
  python scripts/screenshot_only_stage.py queue-clear <copy> after that page

`prepare` also writes tokens.json in the copy, for screenshot_only_shoot.mjs. Private links stay out of the repository.
"""
import json
import shutil
import sqlite3
import sys
from pathlib import Path

NOTE = "The sandbox visit did not complete; nothing was reproduced."
SENT = ("Thank you for your appeal. The second visit showed neither a form to an external host nor a password field.\n\n"
        "Result of the second visit: Not proven. The service was reinstated.")
WHEN = "2026-11-04T14:20:00+00:00"
FIRST_FILLER = 1000


def clone(db, root, src, new, **changes):
    row = dict(db.execute("SELECT * FROM cases WHERE id = ?", (src,)).fetchone())
    row.update(id=new, **changes)
    if row.get("appeal_token"):
        row["appeal_token"] = f"staged-{new}"
    db.execute(f"INSERT INTO cases ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))
    for table in ("model_outputs", "audit", "events"):
        cols = [c[1] for c in db.execute(f"PRAGMA table_info({table})") if c[1] != "id"]
        names = ",".join(cols)
        db.execute(f"INSERT INTO {table} ({names}) SELECT {names.replace('case_id', str(new))} FROM {table} WHERE case_id = ?",
                   (src,))
    if (root / "cases" / str(src)).exists():
        shutil.copytree(root / "cases" / str(src), root / "cases" / str(new), dirs_exist_ok=True)


def appeal(db, root, case, source, **changes):
    row = dict(db.execute("SELECT * FROM appeals WHERE id = ?", (source,)).fetchone())
    row.pop("id")
    row.update(case_id=case, **changes)
    new = db.execute(f"INSERT INTO appeals ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values())).lastrowid
    folder = root / "cases" / str(case) / f"appeal-{source}"
    if folder.exists():
        shutil.copytree(folder, folder.with_name(f"appeal-{new}"), dirs_exist_ok=True)
    db.execute("UPDATE model_outputs SET role = ? WHERE case_id = ? AND role = ?",
               (f"appeal_letter:{new}", case, f"appeal_letter:{source}"))
    return new


def prepare(db, root):
    """Case numbers are those of the demo data this was written for: 5 Warn, 17 appeal, 28 to 40 the example pages."""
    source = db.execute("SELECT id FROM appeals WHERE case_id = 17").fetchone()["id"]
    templated = db.execute("SELECT output FROM model_outputs WHERE case_id = 6 AND role = 'documents' AND status = 'template'")
    templated = templated.fetchone()[0]

    clone(db, root, 5, 41, status="ready", decided_action=None, decided_by=None, decided_at=None)
    clone(db, root, 32, 42, note=NOTE, signals="[]")
    db.execute("DELETE FROM model_outputs WHERE case_id = 42 AND role != 'claim_extraction'")
    (root / "cases" / "42" / "evidence.json").unlink(missing_ok=True)

    clone(db, root, 33, 43)
    db.execute("UPDATE model_outputs SET status = 'skipped', output = 'null', raw = NULL, problem = ? "
               "WHERE case_id = 43 AND role IN ('claim_extraction', 'vision_claim', 'verdict_claim')",
               ("cost limit of USD 15.0 reached",))
    db.execute("UPDATE model_outputs SET status = 'template', model = 'template, model unavailable', kind = 'template', "
               "output = ?, raw = NULL, problem = ? WHERE case_id = 43 AND role = 'documents'",
               (templated, "cost limit of USD 15.0 reached"))

    clone(db, root, 35, 44)
    db.execute("UPDATE model_outputs SET status = 'rejected', output = 'null', raw = ?, problem = ? "
               "WHERE case_id = 44 AND role = 'verdict_claim'",
               ('{"sentences": [{"text": "The page is malware.", "signals": ["malware_detected"]}]}',
                "cites a signal that does not exist: malware_detected"))

    clone(db, root, 36, 45, level=None, signals=None)
    db.execute("DELETE FROM model_outputs WHERE case_id = 45")

    clone(db, root, 17, 46)
    appeal(db, root, 46, source, level=None, signals=None, receipt=None)
    db.execute("DELETE FROM model_outputs WHERE case_id = 46 AND role LIKE 'appeal_letter%'")

    clone(db, root, 17, 47)
    reinstated = appeal(db, root, 47, source, status="decided", decision="Reinstate", decided_by="analyst", decided_at=WHEN)
    db.execute("INSERT INTO audit (ts, case_id, actor, event, detail) VALUES (?, 47, 'analyst', 'appeal_letter_sent', ?)",
               (WHEN, json.dumps({"appeal_id": reinstated, "simulated": True, "sent_text": SENT})))

    clone(db, root, 17, 48)
    appeal(db, root, 48, source, status="ready", level=None, signals="[]",
           note="The second visit did not complete; nothing can be compared.")
    db.execute("UPDATE model_outputs SET status = 'template', model = 'template, model unavailable', kind = 'template', "
               "problem = 'no second visit to compare', output = ? WHERE case_id = 48 AND role LIKE 'appeal_letter%'",
               (json.dumps({"sentences": [{"text": "We could not compare the two visits.", "factual": False, "signals": []}]}),))

    clone(db, root, 17, 49)
    appeal(db, root, 49, source, status="decided", decision="Keep", decided_by="analyst", decided_at=WHEN, level="Proven")

    clone(db, root, 17, 50)
    db.execute("DELETE FROM audit WHERE case_id = 50 AND event = 'customer_fix_simulated'")

    clone(db, root, 17, 51)
    for _ in range(3):
        appeal(db, root, 51, source, status="decided", decision="Keep", decided_by="analyst", decided_at=WHEN, level="Proven")

    clone(db, root, 5, 52)
    db.execute("UPDATE cases SET appeal_token = 'staged-52' WHERE id = 52")

    clone(db, root, 28, 53, level="Not proven", note=NOTE, signals="[]")
    receipt = json.loads(db.execute("SELECT receipt FROM cases WHERE id = 53").fetchone()[0])
    receipt["teardown"]["container_gone"] = False
    db.execute("UPDATE cases SET receipt = ? WHERE id = 53", (json.dumps(receipt),))
    db.execute("DELETE FROM model_outputs WHERE case_id = 53 AND role != 'claim_extraction'")

    first, second = db.execute("SELECT token, abuse_case FROM comparisons ORDER BY id").fetchall()[:2]
    db.execute("UPDATE comparisons SET abuse_baseline = NULL, legit_baseline = NULL WHERE token = ?", (first["token"],))
    tokens = {"running_comparison": first["token"], "comparison": second["token"],
              "twin": db.execute("SELECT token FROM cases WHERE id = ?", (second["abuse_case"],)).fetchone()[0],
              "test": db.execute("SELECT token FROM cases WHERE mode = 'test' ORDER BY id DESC").fetchone()[0],
              "appeal": db.execute("SELECT appeal_token FROM cases WHERE id = 17").fetchone()[0],
              "running_case": first["abuse_case"]}
    (root / "tokens.json").write_text(json.dumps(tokens))


def running(db, root):
    tokens = json.loads((root / "tokens.json").read_text())
    db.execute("UPDATE cases SET status = 'visiting', level = NULL, signals = NULL, note = NULL WHERE id IN (?, 45)",
               (tokens["running_case"],))
    db.execute("UPDATE appeals SET status = 'visiting', level = NULL, signals = NULL, receipt = NULL, note = NULL "
               "WHERE case_id = 46")


def queue_full(db, root):
    for i in range(25):
        db.execute("INSERT INTO cases (id, created_at, mode, url, host, claim, customer_id, customer_name, status) "
                   "VALUES (?, '2026-11-04', 'filler', 'http://x.test/', 'x.test', '', '', '', 'queued')", (FIRST_FILLER + i,))


def queue_clear(db, root):
    db.execute("DELETE FROM cases WHERE id >= ?", (FIRST_FILLER,))


STEPS = {"prepare": prepare, "running": running, "queue-full": queue_full, "queue-clear": queue_clear}


def main():
    step, folder = sys.argv[1], Path(sys.argv[2])
    if "screenshot-copy" not in folder.name:
        sys.exit("Refused: this only runs on a folder named screenshot-copy.")
    db = sqlite3.connect(folder / "due-process.db")
    db.row_factory = sqlite3.Row
    STEPS[step](db, folder)
    db.commit()
    print(step, "done")


if __name__ == "__main__":
    main()
