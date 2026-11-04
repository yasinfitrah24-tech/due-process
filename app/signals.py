"""Deterministic signals from visit evidence, and the verdict level computed from them.

Only form_to_foreign_host and password_field decide the level. The others are recorded and may be cited.
"""
from urllib.parse import urlsplit

PROVEN, SUSPICIOUS, NOT_PROVEN = "Proven", "Suspicious", "Not proven"


def host(url):
    return urlsplit(url).hostname if url else None


def compute(evidence, reported_url):
    foreign = [d for d in evidence.get("destinations", [])
               if "form_index" in d and not d["same_host_as_page"]]
    kinds = {d["kind"] for d in foreign}
    passwords = evidence.get("password_fields", [])
    redirects = evidence.get("redirects", [])
    hidden = evidence.get("hidden_text", [])
    final_host = host(evidence.get("final_url"))
    return [
        {"id": "form_to_foreign_host", "value": bool(foreign),
         "kind": ("observed" if "observed" in kinds else "declared_in_script") if foreign else None,
         "detail": sorted({d["url"] for d in foreign})},
        {"id": "password_field", "value": bool(passwords), "kind": "observed" if passwords else None,
         "detail": sorted({p["page"] for p in passwords})},
        {"id": "redirect_count", "value": len(redirects), "kind": "observed" if redirects else None,
         "detail": [f"{r['type']}: {r['from']} -> {r['to']}" for r in redirects]},
        {"id": "final_host_differs", "value": final_host is not None and final_host != host(reported_url),
         "kind": "observed", "detail": final_host},
        {"id": "hidden_text_present", "value": bool(hidden), "kind": "observed" if hidden else None,
         "detail": [h["text"] for h in hidden]},
    ]


def level(signals):
    values = {s["id"]: s["value"] for s in signals}
    if values["form_to_foreign_host"] and values["password_field"]:
        return PROVEN
    if values["form_to_foreign_host"]:
        return SUSPICIOUS
    return NOT_PROVEN
