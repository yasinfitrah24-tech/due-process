"""`make eval`, on a laptop with the local stack up. Writes eval/results/eval-latest.json and docs/eval.md.

Every number in docs/eval.md comes from this run, or from the saved v1 run where it says v1. Spending is held by the
ledger in the eval's own database. `--report-only` rebuilds docs/eval.md from the saved results, with no new calls.
"""
import json
import statistics as st
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import inventory, signals  # noqa: E402
from eval import metrics  # noqa: E402

CONTAINMENT_RUNS = 10
LIMIT_USD = 0.50
APPROACHES = (("system", "Due Process"), ("text", "Text-only triage"), ("fetch", "Fetch without browser"))
PROXY_IMAGE = "python:3.12-alpine"


def run(cmd, source):
    r = subprocess.run(cmd, input=source, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        sys.exit(f"{cmd[0]} failed:\n{r.stderr[-2000:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def app_stage(corpus):
    folder = f"/data/eval-{int(time.time())}"
    subprocess.run(["docker", "compose", "exec", "-T", "app", "mkdir", "-p", folder], check=True)
    source = (f"CORPUS = {corpus!r}\nCONTAINMENT_RUNS = {CONTAINMENT_RUNS}\nLIMIT_USD = {LIMIT_USD}\n"
              + (ROOT / "eval" / "stage_app.py").read_text(encoding="utf-8"))
    return run(["docker", "compose", "exec", "-T", "-e", f"DATA_DIR={folder}", "app", "python", "-"], source)


def fetch_stage(corpus):
    """The same inventory gate and the same level rule as the system. Only the browser is taken away."""
    known = [c for c in corpus if inventory.customer_for(inventory.reported_host(c["url"]))]
    source = (f"URLS = {[c['url'] for c in known]!r}\n" + (ROOT / "eval" / "fetch_stage.py").read_text(encoding="utf-8"))
    fetched = {r["url"]: r for r in run(["docker", "run", "--rm", "-i", "--network", "dp-sandbox", PROXY_IMAGE, "python", "-"], source)}
    rows = []
    for c in corpus:
        host = inventory.reported_host(c["url"])
        customer = inventory.customer_for(host)
        page = fetched.get(c["url"])
        if customer is None:
            rows.append({"id": c["id"], "action": "none", "target": None, "level": None, "wall_s": 0})
            continue
        if "error" in page:
            rows.append({"id": c["id"], "action": "Close", "target": customer["id"], "level": signals.NOT_PROVEN,
                         "error": page["error"], "wall_s": page["wall_s"]})
            continue
        final = page["final_url"]
        evidence = {"final_url": final, "password_fields": [{"page": final}] * page["passwords"],
                    "destinations": [{"page": final, "url": a, "form_index": i, "kind": "observed"}
                                     for i, a in enumerate(page["actions"])]}
        level = signals.level(signals.compute(evidence, c["url"]))
        action = {signals.PROVEN: "Suspend", signals.SUSPICIOUS: "Warn", signals.NOT_PROVEN: "Close"}[level]
        rows.append({"id": c["id"], "action": action, "target": customer["id"], "level": level, "wall_s": page["wall_s"]})
    return rows


def mean(rows, key, digits=2):
    values = [r[key] for r in rows if r.get(key) is not None]
    return round(st.mean(values), digits) if values else 0


def count(s, key, total=None):
    return f"{len(s[key])} of {s[total]}" if total else str(len(s[key]))


def compare_row(name, v1, v2):
    return (f"| {name} | {count(v1, 'wrongful_suspend', 'benign_cases')} | {count(v2, 'wrongful_suspend', 'benign_cases')} "
            f"| {count(v1, 'missed_abuse', 'abuse_cases')} | {count(v2, 'missed_abuse', 'abuse_cases')} "
            f"| {count(v1, 'injection_changed_outcome', 'injection_cases')} "
            f"| {count(v2, 'injection_changed_outcome', 'injection_cases')} "
            f"| {count(v1, 'false_warn')} | {count(v2, 'false_warn')} "
            f"| {count(v1, 'action_on_wrong_target')} | {count(v2, 'action_on_wrong_target')} |")


def pair_verdict(pairs):
    """One sentence per approach, from the numbers."""
    right = {k: sum(p["both_right"] for p in v) for k, v in pairs.items()}
    n = len(pairs["system"])
    if right["system"] > right["text"]:
        lead = f"On these pairs Due Process did better than text-only triage: {right['system']} of {n} against {right['text']}."
    elif right["system"] == right["text"]:
        lead = f"On these pairs Due Process and text-only triage did equally well: {right['system']} of {n} each."
    else:
        lead = f"On these pairs text-only triage did better than Due Process: {right['text']} of {n} against {right['system']}."
    return lead


def ids(items):
    return ", ".join(f"`{i}`" for i in items) or "none"


def report(data, v1):
    s, t, f = data["scores"]["system"], data["scores"]["text"], data["scores"]["fetch"]
    pairs = {key: metrics.pairs(data["corpus"], data[key]) for key, _ in APPROACHES}
    twins = sum(1 for c in data["corpus"] if c.get("pair"))
    fresh = [c for c in data["corpus"] if c["category"] in ("twin", "mixed")]
    old = [c for c in data["corpus"] if c not in fresh]
    new_scores = {key: metrics.score(fresh, [r for r in data[key] if r["id"] in {c["id"] for c in fresh}])
                  for key, _ in APPROACHES}
    old_scores = {key: metrics.score(old, [r for r in data[key] if r["id"] in {c["id"] for c in old}])
                  for key, _ in APPROACHES}
    system, text, fetch, cont = data["system"], data["text"], data["fetch"], data["containment"]
    roles = sum(len(r["outputs"]) for r in system)
    rejected = [(r["id"], k, o["problem"]) for r in system for k, o in r["outputs"].items() if o["status"] == "rejected"]
    templates = [(r["id"], r["outputs"]["documents"]["problem"]) for r in system
                 if r["outputs"].get("documents", {}).get("status") == "template"]
    lines = [
        "# Evaluation", "",
        f"Generated by `make eval` on {data['date']} at commit `{data['commit']}`. Do not edit by hand. Raw output: "
        "`eval/results/eval-latest.json`.", "",
        f"Version 2: {len(data['corpus'])} labeled cases, all on fictional TEST PAGE sites, including cases the system is "
        "known to miss. These are the 26 cases of `corpus/cases.json` and the new ones in `corpus/cases-v2.json`. One run per "
        "case: a small set that shows behavior, not a rate for real traffic. Version 1 is kept as it was in "
        "`eval/results/eval-v1-2026-11-04.json`.", "",
        f"Models: `{data['model']}` for text and `{data['vision_model']}` for images, on Vultr Serverless Inference. "
        f"Total model cost of this run: USD {data['cost_usd']:.4f}, limit USD {LIMIT_USD:.2f}.", "",
        "## Why version 2 exists", "",
        "In version 1, text-only triage did as well as Due Process, and the corpus allowed a shortcut: every phishing page "
        "was on a Tailspin host, almost every false report was on a Northwind Bank host, and the text model is given the "
        "customer list. It could score well by judging who owns the host.", "",
        "Version 2 is designed to remove that shortcut, and it says so openly: **the twin pairs test what cannot be known "
        "from the text.** That is a test written to expose a limit of reading reports, so it favors any approach that opens "
        "the page. It measures that limit. It is not a sample of real abuse reports, where a report's wording does carry "
        "information.", "",
        f"- **{twins // 2} twin pairs.** Both pages of a pair get the identical report. One page is abuse and one is "
        "legitimate. Both are owned by one customer with a neutral name, on neutrally named hosts, with paths that are only "
        "numbers, and abuse pages are split evenly across the two hosts. Nothing in the input to the text model separates "
        "the two pages.",
        "- **5 mixed cases** break the old pattern: phishing on Northwind Bank hosts with a flat report, and strong false "
        "reports against legitimate pages on Tailspin hosts.",
        "- The text model's input did not change: report text, URL and customer list.", "",
        "## Twin pairs", "",
        "A pair is right only if the abuse page gets an action and the legitimate page is closed.", "",
        "| Approach | Pairs right | Same action on both pages |", "|---|---|---|",
        *[f"| {name} | {sum(p['both_right'] for p in pairs[key])} of {len(pairs[key])} "
          f"| {sum(p['same_action'] for p in pairs[key])} of {len(pairs[key])} |" for key, name in APPROACHES],
        "", pair_verdict(pairs), "",
        "| Pair | What differs | Due Process | Text-only triage | Fetch without browser |", "|---|---|---|---|---|",
        *[f"| {a['pair']} | {a['kind']} | {a['abuse_action']} / {a['legit_action']} | {b['abuse_action']} / {b['legit_action']} "
          f"| {c['abuse_action']} / {c['legit_action']} |"
          for a, b, c in zip(pairs["system"], pairs["text"], pairs["fetch"])],
        "", "Each cell shows the action on the abuse page, then on the legitimate page.", "",
        "## Version 1 and version 2", "",
        f"Version 1 has {v1['scores']['system']['cases']} cases, version 2 has {s['cases']}.", "",
        "| Approach | Wrongful suspend v1 | v2 | Abuse missed v1 | v2 | Injection changed outcome v1 | v2 | False warn v1 | v2 "
        "| Action on wrong target v1 | v2 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
        *[compare_row(name, v1["scores"][key], data["scores"][key]) for key, name in APPROACHES], "",
        f"The same version 2 run, split into the {len(old)} version 1 cases and the {len(fresh)} new cases (twin pairs and "
        "mixed). Text-only triage does well on the first group and badly on the second, so the total depends on how many "
        "cases of each kind a corpus holds.", "",
        "| Approach | Wrongful suspend, v1 cases | new cases | Abuse missed, v1 cases | new cases |", "|---|---|---|---|---|",
        *[f"| {name} | {count(old_scores[key], 'wrongful_suspend', 'benign_cases')} "
          f"| {count(new_scores[key], 'wrongful_suspend', 'benign_cases')} "
          f"| {count(old_scores[key], 'missed_abuse', 'abuse_cases')} "
          f"| {count(new_scores[key], 'missed_abuse', 'abuse_cases')} |" for key, name in APPROACHES], "",
        "| Approach | Mean s per case | Cost USD per case |", "|---|---|---|",
        f"| Due Process | {mean([r for r in system if not r['rejected']], 'wall_s')} "
        f"| {mean([r for r in system if not r['rejected']], 'cost_usd', 6):.6f} |",
        f"| Text-only triage | {mean(text, 'wall_s')} | {mean(text, 'cost_usd', 6):.6f} |",
        f"| Fetch without browser | {mean(fetch, 'wall_s', 3)} | 0 |", "",
        "How to read this:", "",
        "- For Due Process the action is the strongest one its level allows, so this is the worst an analyst could do. A real "
        "analyst approves each action and sees the evidence.",
        "- Text-only triage: a model reads the URL and the report and decides. It gets the customer list. It has no defense "
        "against injection in its prompt.",
        "- Fetch without browser: one HTTP GET through the same proxy, with the same inventory check and the same level rule "
        "as Due Process. Only the browser is taken away.",
        "- Abuse missed means no Suspend or Warn on a case labeled as abuse.",
        "- Fetch without browser never reads the report. Its injection count is a page it missed, not an injection that "
        "worked.", "",
        "## What these numbers do not show", "",
        "- **The twin results are close to fixed by the design, on both sides.** Text-only triage gets almost the same input "
        "for both pages of a pair, so it can hardly answer them differently, and a pair needs different answers. That is "
        "the point of the test, and says nothing about how good the model is at reading reports.",
        "- **Due Process's pairs are its easy case.** The abuse pages use techniques its signals are built to see. None "
        "uses the two known misses, a URL built at run time and a login in an iframe, which stay in the totals as "
        "`phish-runtime-url` and `phish-iframe`.",
        "- The twin pairs show a limit of reading reports, not how often real reports are like this.",
        "- The text model gives no evidence for its decision. That difference does not show in these columns.",
        "- Every case ran once, on simple pages. The corpus was extended after seeing the v1 results.", "",
        "## Cases each approach got wrong", "",
        "| Approach | Wrongful suspend | Abuse missed | Injection | False warn |", "|---|---|---|---|---|",
        *[f"| {n} | {ids(x['wrongful_suspend'])} | {ids(x['missed_abuse'])} | {ids(x['injection_changed_outcome'])} "
          f"| {ids(x['false_warn'])} |" for n, x in (("Due Process", s), ("Text-only triage", t), ("Fetch without browser", f))],
        "", "## Model claims in Due Process", "",
        f"- Citations of signals that do not exist: {len(data['fake_citations'])} across {roles} model outputs.",
        f"- Final outputs rejected: {len(rejected)}. Documents that fell back to the template after two failed tries: "
        f"{len(templates)}. These last reasons come from the word check, which can also refuse a harmless sentence:",
    ]
    lines += [f"  - `{i}`, {role}: {problem}" for i, role, problem in rejected]
    lines += [f"  - `{i}`: {problem}" for i, problem in templates]
    runs = len(cont)
    lines += [
        "", f"## Containment, {runs} runs", "",
        f"- Level: {sum(c['level'] == 'Proven' for c in cont)} of {runs} Proven. Changed: "
        f"{sum(c['level'] != 'Proven' for c in cont)}.",
        f"- `vision_claim` repeated the page's safe or verified claim, by the page-text flag: "
        f"{sum(c['flagged_safe_repeat'] for c in cont)} of {runs}.",
        f"- `vision_claim` used the word safe or verified at all: {sum(c['mentions_safe'] for c in cont)} of {runs}.",
        f"- Metadata request blocked: {sum(bool(c['egress_blocked']) for c in cont)} of {runs}. "
        f"Download quarantined: {sum(bool(c['download_quarantined']) for c in cont)} of {runs}.",
        f"- Documents accepted: {sum(c['documents'] == 'accepted' for c in cont)} of {runs}. Template: "
        f"{sum(c['documents'] == 'template' for c in cont)} of {runs}.",
        f"- Mean time {mean(cont, 'wall_s')} s, mean cost USD {mean(cont, 'cost_usd', 6):.6f}.", "",
    ]
    return "\n".join(lines)


def main():
    results = ROOT / "eval" / "results"
    v1 = json.loads((results / "eval-v1-2026-11-04.json").read_text(encoding="utf-8"))
    if "--report-only" in sys.argv:
        data = json.loads((results / "eval-latest.json").read_text(encoding="utf-8"))
        (ROOT / "docs" / "eval.md").write_text(report(data, v1), encoding="utf-8")
        return
    corpus = [c for name in ("cases.json", "cases-v2.json")
              for c in json.loads((ROOT / "corpus" / name).read_text(encoding="utf-8"))]
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    stage = app_stage(corpus)
    fetch = fetch_stage(corpus)
    data = {"date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "commit": commit, "corpus": corpus, **stage,
            "fetch": fetch,
            "scores": {"system": metrics.score(corpus, stage["system"]), "text": metrics.score(corpus, stage["text"]),
                       "fetch": metrics.score(corpus, fetch)},
            "fake_citations": metrics.fake_citations(stage["system"])}
    results.mkdir(exist_ok=True)
    (results / "eval-latest.json").write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (ROOT / "docs" / "eval.md").write_text(report(data, v1), encoding="utf-8")
    print(f"cases {len(corpus)}, cost USD {data['cost_usd']:.4f}")
    for key, name in APPROACHES:
        print(name, "pairs right", sum(p["both_right"] for p in metrics.pairs(corpus, data[key])))
    for name, s in data["scores"].items():
        print(name, {k: (len(v) if isinstance(v, list) else v) for k, v in s.items()})


if __name__ == "__main__":
    main()
