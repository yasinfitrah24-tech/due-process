"""SQLite storage: cases, audit log, model outputs, model usage and prices."""
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

JSON_FIELDS = ("signals", "receipt")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'demo',
  token TEXT,
  url TEXT NOT NULL,
  host TEXT NOT NULL,
  claim TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  customer_name TEXT NOT NULL,
  status TEXT NOT NULL,
  level TEXT,
  note TEXT,
  signals TEXT,
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
CREATE TABLE IF NOT EXISTS model_prices (
  model TEXT PRIMARY KEY,
  prompt_usd REAL NOT NULL,
  completion_usd REAL NOT NULL,
  fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  case_id INTEGER,
  role TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_tokens INTEGER NOT NULL,
  completion_tokens INTEGER NOT NULL,
  cost_usd REAL NOT NULL,
  latency_s REAL NOT NULL,
  ok INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS model_outputs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  case_id INTEGER NOT NULL,
  role TEXT NOT NULL,
  model TEXT NOT NULL,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  output TEXT,
  raw TEXT,
  problem TEXT,
  unknown_citations TEXT NOT NULL,
  latency_s REAL,
  cost_usd REAL
);
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
        columns = [r["name"] for r in c.execute("PRAGMA table_info(cases)")]
        if "mode" not in columns:
            c.execute("ALTER TABLE cases ADD COLUMN mode TEXT NOT NULL DEFAULT 'demo'")
        if "token" not in columns:
            c.execute("ALTER TABLE cases ADD COLUMN token TEXT")
        for row in c.execute("SELECT id FROM cases WHERE mode = 'test' AND token IS NULL").fetchall():
            c.execute("UPDATE cases SET token = ? WHERE id = ?", (secrets.token_urlsafe(16), row["id"]))


def as_case(row):
    if row is None:
        return None
    case = dict(row)
    for field in JSON_FIELDS:
        case[field] = json.loads(case[field]) if case[field] else None
    case["ref"] = case["token"] or str(case["id"])
    return case


def create_case(url, host, claim, customer, mode="demo"):
    with connect() as c:
        token = secrets.token_urlsafe(16) if mode == "test" else None
        cur = c.execute("INSERT INTO cases (created_at, mode, token, url, host, claim, customer_id, customer_name, "
                        "status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued')",
                        (now(), mode, token, url, host, claim, customer["id"], customer["name"]))
        return cur.lastrowid


def waiting_cases():
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM cases WHERE status IN ('queued', 'visiting')").fetchone()[0]


def update_case(case_id, **fields):
    values = [json.dumps(v) if k in JSON_FIELDS else v for k, v in fields.items()]
    sets = ", ".join(f"{k} = ?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE cases SET {sets} WHERE id = ?", (*values, case_id))


def get_case(case_id):
    with connect() as c:
        return as_case(c.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone())


def get_case_by_ref(ref):
    """A demo case by number, a Test any URL case only by its random token: those are never listed or guessable."""
    with connect() as c:
        if ref.isdigit():
            row = c.execute("SELECT * FROM cases WHERE id = ? AND mode = 'demo'", (int(ref),)).fetchone()
        else:
            row = c.execute("SELECT * FROM cases WHERE token = ?", (ref,)).fetchone()
    return as_case(row)


def list_cases():
    """The public queue: demo cases only."""
    with connect() as c:
        return [as_case(r) for r in c.execute("SELECT * FROM cases WHERE mode = 'demo' ORDER BY id DESC LIMIT 100")]


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


def get_price(model):
    with connect() as c:
        row = c.execute("SELECT prompt_usd, completion_usd FROM model_prices WHERE model = ?", (model,)).fetchone()
    return (row["prompt_usd"], row["completion_usd"]) if row else None


def save_price(model, prompt_usd, completion_usd):
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO model_prices VALUES (?, ?, ?, ?)", (model, prompt_usd, completion_usd, now()))


def record_usage(case_id, role, model, prompt_tokens, completion_tokens, cost_usd, latency_s, ok=True):
    with connect() as c:
        c.execute("INSERT INTO llm_usage (ts, case_id, role, model, prompt_tokens, completion_tokens, cost_usd, "
                  "latency_s, ok) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (now(), case_id, role, model, prompt_tokens, completion_tokens, cost_usd, latency_s, int(ok)))


def total_cost():
    with connect() as c:
        return c.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage").fetchone()[0]


def cost_today():
    with connect() as c:
        return c.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage WHERE substr(ts, 1, 10) = ?",
                         (now()[:10],)).fetchone()[0]


def case_calls(case_id):
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM llm_usage WHERE case_id = ?", (case_id,)).fetchone()[0]


def case_usage(case_id):
    with connect() as c:
        row = c.execute("SELECT COUNT(*) AS calls, COALESCE(SUM(cost_usd), 0) AS cost_usd, "
                        "COALESCE(SUM(latency_s), 0) AS latency_s FROM llm_usage WHERE case_id = ?",
                        (case_id,)).fetchone()
    return dict(row)


def save_output(case_id, role, model, entry):
    with connect() as c:
        c.execute("INSERT INTO model_outputs (ts, case_id, role, model, kind, status, output, raw, problem, "
                  "unknown_citations, latency_s, cost_usd) VALUES (?, ?, ?, ?, 'model_claim', ?, ?, ?, ?, ?, ?, ?)",
                  (now(), case_id, role, model, entry["status"], json.dumps(entry["output"]), entry["raw"],
                   entry["problem"], json.dumps(entry["unknown_citations"]), entry["latency_s"], entry["cost_usd"]))


def model_outputs(case_id):
    """{role: output row}."""
    with connect() as c:
        rows = c.execute("SELECT * FROM model_outputs WHERE case_id = ? ORDER BY id", (case_id,)).fetchall()
    return {r["role"]: {**dict(r), "output": json.loads(r["output"]),
                        "unknown_citations": json.loads(r["unknown_citations"])} for r in rows}


def fail_interrupted():
    """Cases left mid-run by a restart can never finish. A level already computed stands."""
    with connect() as c:
        rows = c.execute("SELECT id, level FROM cases WHERE status IN ('queued', 'visiting', 'drafting')").fetchall()
    for r in rows:
        if r["level"]:
            update_case(r["id"], status="ready")
        else:
            update_case(r["id"], status="ready", level="Not proven", note="Interrupted by a restart; no evidence.")
        audit(r["id"], "system", "case_interrupted")
