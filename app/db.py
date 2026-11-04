"""SQLite storage: cases, audit log, model outputs, model usage and prices."""
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

JSON_FIELDS = ("signals", "receipt")
PENDING_APPEALS = ("requested", "visiting", "drafting")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'demo',
  token TEXT,
  appeal_token TEXT,
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
CREATE TABLE IF NOT EXISTS appeals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  choice TEXT NOT NULL,
  text TEXT NOT NULL,
  status TEXT NOT NULL,
  level TEXT,
  note TEXT,
  signals TEXT,
  receipt TEXT,
  decision TEXT,
  decided_by TEXT,
  decided_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  case_id INTEGER NOT NULL,
  appeal_id INTEGER,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_case ON events (case_id);
CREATE TABLE IF NOT EXISTS comparisons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token TEXT NOT NULL,
  created_at TEXT NOT NULL,
  twin TEXT NOT NULL,
  abuse_case INTEGER NOT NULL,
  legit_case INTEGER NOT NULL,
  abuse_baseline TEXT,
  legit_baseline TEXT
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
        if "appeal_token" not in columns:
            c.execute("ALTER TABLE cases ADD COLUMN appeal_token TEXT")
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
        token = secrets.token_urlsafe(16) if mode != "demo" else None
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


def clear_cases():
    """Deletes every case and restarts the numbering. The cost ledger stays. Returns how many went."""
    with connect() as c:
        count = c.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        for table in ("cases", "audit", "model_outputs", "appeals", "events", "comparisons"):
            c.execute(f"DELETE FROM {table}")
            c.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
        # Numbers start again, so old spending must not count against a new case with the same number.
        c.execute("UPDATE llm_usage SET case_id = NULL")
    return count


def add_event(case_id, appeal_id, event):
    """event has source, kind and ts; everything else is its detail."""
    detail = {k: v for k, v in event.items() if k not in ("ts", "source", "kind")}
    with connect() as c:
        c.execute("INSERT INTO events (ts, case_id, appeal_id, source, kind, detail) VALUES (?, ?, ?, ?, ?, ?)",
                  (event.get("ts") or time.time(), case_id, appeal_id, event["source"], event["kind"], json.dumps(detail)))


def replace_proxy_events(case_id, appeal_id, events):
    """Swaps what was followed live for the proxy's log, which is the record."""
    with connect() as c:
        c.execute("DELETE FROM events WHERE case_id = ? AND appeal_id IS ? AND source = 'proxy'", (case_id, appeal_id))
    for event in events:
        add_event(case_id, appeal_id, event)


def case_events(case_id):
    with connect() as c:
        rows = c.execute("SELECT * FROM events WHERE case_id = ? ORDER BY ts, id", (case_id,)).fetchall()
    return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]


def create_comparison(twin, abuse_case, legit_case):
    token = secrets.token_urlsafe(16)
    with connect() as c:
        c.execute("INSERT INTO comparisons (token, created_at, twin, abuse_case, legit_case) VALUES (?, ?, ?, ?, ?)",
                  (token, now(), twin, abuse_case, legit_case))
    return token


def get_comparison(token):
    with connect() as c:
        row = c.execute("SELECT * FROM comparisons WHERE token = ?", (token,)).fetchone()
    if row is None:
        return None
    return {**dict(row), **{k: json.loads(row[k]) if row[k] else None for k in ("abuse_baseline", "legit_baseline")}}


def set_baseline(token, side, result):
    assert side in ("abuse", "legit")
    with connect() as c:
        c.execute(f"UPDATE comparisons SET {side}_baseline = ? WHERE token = ?", (json.dumps(result), token))


def set_appeal_token(case_id):
    with connect() as c:
        c.execute("UPDATE cases SET appeal_token = ? WHERE id = ?", (secrets.token_urlsafe(16), case_id))


def get_case_by_appeal_token(token):
    with connect() as c:
        return as_case(c.execute("SELECT * FROM cases WHERE appeal_token = ?", (token,)).fetchone())


def as_appeal(row):
    if row is None:
        return None
    appeal = dict(row)
    for field in JSON_FIELDS:
        appeal[field] = json.loads(appeal[field]) if appeal[field] else None
    return appeal


def create_appeal(case_id, choice, text):
    with connect() as c:
        return c.execute("INSERT INTO appeals (case_id, created_at, choice, text, status) VALUES (?, ?, ?, ?, 'requested')",
                         (case_id, now(), choice, text)).lastrowid


def get_appeal(appeal_id):
    with connect() as c:
        return as_appeal(c.execute("SELECT * FROM appeals WHERE id = ?", (appeal_id,)).fetchone())


def case_appeals(case_id):
    with connect() as c:
        return [as_appeal(r) for r in c.execute("SELECT * FROM appeals WHERE case_id = ? ORDER BY id", (case_id,))]


def update_appeal(appeal_id, **fields):
    values = [json.dumps(v) if k in JSON_FIELDS else v for k, v in fields.items()]
    sets = ", ".join(f"{k} = ?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE appeals SET {sets} WHERE id = ?", (*values, appeal_id))


def decide_appeal(appeal_id, decision, analyst):
    """Records the decision once. False if the appeal is not waiting for one."""
    with connect() as c:
        cur = c.execute("UPDATE appeals SET decision = ?, decided_by = ?, decided_at = ?, status = 'decided' "
                        "WHERE id = ? AND status = 'ready' AND decision IS NULL", (decision, analyst, now(), appeal_id))
        return cur.rowcount == 1


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


def reserve_usage(case_id, role, model, worst_cost, case_limit, role_limit, total_limit, daily_limit):
    """Takes a place for one model call, in one transaction, so calls running side by side cannot pass a limit together.
    The row counts at its worst cost until finish_usage. Returns (row id, None) or (None, the limit that stopped it)."""
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            def scalar(sql, *args):
                return c.execute(sql, args).fetchone()[0]

            if role_limit is not None and scalar("SELECT COUNT(*) FROM llm_usage WHERE case_id = ? AND role = ?",
                                                 case_id, role) >= role_limit:
                return None, f"appeal limit of {role_limit} model calls reached"
            if case_limit is not None and scalar("SELECT COUNT(*) FROM llm_usage WHERE case_id = ? AND role NOT LIKE "
                                                 "'appeal_letter%'", case_id) >= case_limit:
                return None, f"case limit of {case_limit} model calls reached"
            if total_limit is not None and scalar("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage") + worst_cost > total_limit:
                return None, f"cost limit of USD {total_limit} reached"
            if daily_limit is not None and scalar("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage WHERE substr(ts, 1, 10) = ?",
                                                  now()[:10]) + worst_cost > daily_limit:
                return None, f"daily cost limit of USD {daily_limit} reached"
            row = c.execute("INSERT INTO llm_usage (ts, case_id, role, model, prompt_tokens, completion_tokens, cost_usd, "
                            "latency_s, ok) VALUES (?, ?, ?, ?, 0, 0, ?, 0, 1)", (now(), case_id, role, model, worst_cost))
            return row.lastrowid, None
        finally:
            c.execute("COMMIT")


def finish_usage(usage_id, prompt_tokens, completion_tokens, cost_usd, latency_s, ok=True):
    with connect() as c:
        c.execute("UPDATE llm_usage SET prompt_tokens = ?, completion_tokens = ?, cost_usd = ?, latency_s = ?, ok = ? "
                  "WHERE id = ?", (prompt_tokens, completion_tokens, cost_usd, latency_s, int(ok), usage_id))


def total_cost():
    with connect() as c:
        return c.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage").fetchone()[0]


def cost_today():
    with connect() as c:
        return c.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage WHERE substr(ts, 1, 10) = ?",
                         (now()[:10],)).fetchone()[0]


def case_calls(case_id):
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM llm_usage WHERE case_id = ? AND role NOT LIKE 'appeal_letter%'",
                         (case_id,)).fetchone()[0]


def role_calls(case_id, role):
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM llm_usage WHERE case_id = ? AND role = ?", (case_id, role)).fetchone()[0]


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
    with connect() as c:
        marks = ",".join("?" * len(PENDING_APPEALS))
        rows = c.execute(f"SELECT id, case_id FROM appeals WHERE status IN ({marks})", PENDING_APPEALS).fetchall()
    for r in rows:
        # No level: an interrupted second visit proves nothing, so it cannot open Reinstate.
        update_appeal(r["id"], status="ready", level=None, note="Interrupted by a restart; no second visit.")
        audit(r["case_id"], "system", "appeal_interrupted", appeal_id=r["id"])
