"""Plain sentences written by code from the signals. No model is involved, so they cannot say more than the signals do."""
import re

LONG_URL = re.compile(r"https?://\S{70,}")


def short_urls(text):
    """For display only: a long URL inside a sentence is cut, so one report cannot fill a page. The record keeps it whole."""
    return LONG_URL.sub(lambda m: f"{m[0][:70]}\u2026", text)


def why(found):
    """One or two sentences on what the visit found, or None if there is no evidence."""
    values = {s["id"]: s["value"] for s in found or []}
    if not values:
        return None
    if values["form_to_foreign_host"] and values["password_field"]:
        first = "A form on the page sends data to another host, and the page asks for a password."
    elif values["form_to_foreign_host"]:
        first = "A form on the page sends data to another host. The page asks for no password."
    elif values["password_field"]:
        first = "The page asks for a password, and no form sends data to another host."
    else:
        first = "The visit found no form that sends data to another host."

    extras = []
    if values.get("hidden_text_present"):
        extras.append("holds hidden text")
    if values.get("egress_blocked"):
        extras.append("tried to reach an address it may not reach")
    if values.get("download_quarantined"):
        extras.append("started a download that was held and never opened")
    if not extras:
        return first
    listed = extras[0] if len(extras) == 1 else ", ".join(extras[:-1]) + " and " + extras[-1]
    return f"{first} The page also {listed}."


LABELS = {
    "form_to_foreign_host": "Form to a foreign host",
    "password_field": "Password field",
    "final_host_differs": "Ended on another host",
    "hidden_text_present": "Hidden text",
    "egress_blocked": "Request blocked by the proxy",
    "download_quarantined": "Download held",
}


def present(found):
    """[{"label", "kind"}] for the signals that are present, in words."""
    rows = []
    for s in found or []:
        if s["value"]:
            count = s["value"] if s["id"] == "redirect_count" else None
            label = f"Redirects: {count}" if count else LABELS[s["id"]]
            rows.append({"label": label, "kind": s["kind"]})
    return rows


def contained(evidence, receipt):
    """[{"attempt", "result", "code", "more"}]: what the page tried and what stopped it. Empty if it tried nothing."""
    rows = [{"attempt": f"Reach a {e['reason']}" if e.get("reason") else "Reach a blocked address",
             "result": "Blocked by the proxy.", "code": f"{e['method']} {e['host']}:{e['port']}{e.get('path') or ''}",
             "more": None}
            for e in evidence.get("egress", []) if e.get("decision") == "blocked"]
    receipt = receipt or {}
    rows += [{"attempt": "Start a download", "result": "Held.", "code": d["name"],
              "more": f"{d['bytes']} bytes. Never opened. Only the hash left the container; the file went with it."}
             for d in receipt.get("quarantined_downloads", [])]
    if rows and receipt.get("teardown", {}).get("container_gone"):
        rows.append({"attempt": "The browser itself", "result": "Destroyed when the visit ended.", "code": None,
                     "more": sandbox_line(receipt)})
    return rows


def sandbox_line(receipt):
    flags = receipt.get("docker_flags", [])
    parts = []
    if receipt.get("chromium_sandbox", {}).get("in_new_user_pid_net_ns"):
        parts.append("Chromium sandbox on")
    if "ALL" in flags:
        parts.append("no capabilities")
    if "--read-only" in flags:
        parts.append("read-only disk")
    return ", ".join(parts) + "." if parts else None


def status_line(case):
    if case["status"] == "queued":
        return "Received. Waiting for a sandbox."
    if case["status"] == "visiting":
        return "Received. The browser is visiting the page."
    if case["status"] == "drafting":
        return "Received, reproduced. Model claims are being drafted."
    reproduced = "Received. Nothing was reproduced." if case["note"] else "Received, reproduced, decided."
    if case["status"] == "decided":
        return f"{reproduced} Action taken (simulated)."
    return f"{reproduced} Awaiting an analyst." if case["mode"] == "demo" else reproduced


def live_line(case):
    """What a screen reader hears when this changes: the level, as soon as there is one."""
    line = status_line(case)
    return f"Level: {case['level']}. {line}" if case["level"] else line


def customer_headline(case, appeals, closed):
    if any(a["decision"] == "Reinstate" for a in appeals):
        return "Your page was reinstated (simulated)."
    first = "Your page was suspended." if case["decided_action"] == "Suspend" else "You received a warning."
    return first if closed else f"{first} You can appeal."


def appeal_steps(case, appeals):
    visited = any(a["status"] in ("drafting", "ready", "decided") for a in appeals)
    action = "Suspended." if case["decided_action"] == "Suspend" else "Warned."
    return [{"title": action, "text": "On browser evidence, approved by an analyst.", "done": True},
            {"title": "Appeal sent.", "text": "Your text is stored as a claim.", "done": bool(appeals)},
            {"title": "Re-visited.", "text": "A new disposable browser opens the page.", "done": visited},
            {"title": "Decided.", "text": "Reinstate only if the re-visit finds a lower level than before.",
             "done": any(a["decision"] for a in appeals), "reinstated": any(a["decision"] == "Reinstate" for a in appeals)}]


def appeal_status(appeals):
    if not appeals:
        return "No appeal yet."
    last = appeals[-1]
    level = f" Second visit: {last['level']}." if last["level"] else ""
    return f"Appeal {len(appeals)}: {last['status']}.{level}"
