"""SQLite storage: cases, audit log, LLM call budget."""
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

LLM_CALL_LIMIT = 30
JSON_FIELDS = ("signals", "plan", "receipt")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  url TEXT NOT NULL,
  host TEXT NOT NULL,
  claim TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  customer_name TEXT NOT NULL,
  status TEXT NOT NULL,
  level TEXT,
  note TEXT,
  signals TEXT,
  plan TEXT,
  receipt TEXT,
  decided_action TEXT,
  decided_by TEXT,
  decided_at TEXT
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  case_id INTEGER,
  actor TEXT NOT NULL,
  event TEXT NOT NULL,
  detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def data_dir():
    return Path(os.environ.get("DATA_DIR", "data"))


def case_dir(case_id):
    return data_dir() / "cases" / str(case_id)


def connect():
    conn = sqlite3.connect(data_dir() / "due-process.db", timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    data_dir().mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.executescript(SCHEMA)


def as_case(row):
    if row is None:
        return None
    case = dict(row)
    for field in JSON_FIELDS:
        case[field] = json.loads(case[field]) if case[field] else None
    return case


def create_case(url, host, claim, customer):
    with connect() as c:
        cur = c.execute("INSERT INTO cases (created_at, url, host, claim, customer_id, customer_name, status) "
                        "VALUES (?, ?, ?, ?, ?, ?, 'queued')", (now(), url, host, claim, customer["id"], customer["name"]))
        return cur.lastrowid


def update_case(case_id, **fields):
    values = [json.dumps(v) if k in JSON_FIELDS else v for k, v in fields.items()]
    sets = ", ".join(f"{k} = ?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE cases SET {sets} WHERE id = ?", (*values, case_id))


def get_case(case_id):
    with connect() as c:
        return as_case(c.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone())


def list_cases():
    with connect() as c:
        return [as_case(r) for r in c.execute("SELECT * FROM cases ORDER BY id DESC LIMIT 100")]


def decide(case_id, action, analyst):
    """Records the decision once. False if the case already has one."""
    with connect() as c:
        cur = c.execute("UPDATE cases SET decided_action = ?, decided_by = ?, decided_at = ?, status = 'decided' "
                        "WHERE id = ? AND decided_action IS NULL", (action, analyst, now(), case_id))
        return cur.rowcount == 1


def audit(case_id, actor, event, **detail):
    with connect() as c:
        c.execute("INSERT INTO audit (ts, case_id, actor, event, detail) VALUES (?, ?, ?, ?, ?)",
                  (now(), case_id, actor, event, json.dumps(detail)))


def audit_log(case_id):
    with connect() as c:
        rows = c.execute("SELECT * FROM audit WHERE case_id = ? ORDER BY id", (case_id,)).fetchall()
    return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]


def take_llm_call():
    """Counts one real model call against the limit. False once it is used up."""
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        used = c.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
        if used >= LLM_CALL_LIMIT:
            c.execute("ROLLBACK")
            return False
        c.execute("INSERT INTO llm_calls (ts) VALUES (?)", (now(),))
        c.execute("COMMIT")
        return True


def fail_interrupted():
    """Cases left mid-visit by a restart can never finish."""
    with connect() as c:
        rows = c.execute("SELECT id FROM cases WHERE status IN ('queued', 'visiting')").fetchall()
    for r in rows:
        update_case(r["id"], status="ready", level="Not proven", note="Interrupted by a restart; no evidence.")
        audit(r["id"], "system", "case_interrupted")
