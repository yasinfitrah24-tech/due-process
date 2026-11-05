"""Deterministic signals from visit evidence, and the verdict level computed from them.

Only form_to_foreign_host and password_field decide the level. The others are recorded and may be cited.
"""
from urllib.parse import urlsplit

from app import inventory

PROVEN, SUSPICIOUS, NOT_PROVEN = "Proven", "Suspicious", "Not proven"


def host(url):
    return urlsplit(url).hostname if url else None


def compute(evidence, reported_url):
    foreign = [d for d in evidence.get("destinations", [])
               if "form_index" in d and inventory.is_foreign(host(d["page"]), host(d["url"]))]
    kinds = {d["kind"] for d in foreign}
    passwords = evidence.get("password_fields", [])
    redirects = evidence.get("redirects", [])
    hidden = evidence.get("hidden_text", [])
    blocked = [e for e in evidence.get("egress", []) if e.get("decision") == "blocked"]
    downloads = evidence.get("downloads", [])
    final_host = host(evidence.get("final_url"))
    same_site = inventory.registrable_domain(final_host) == inventory.registrable_domain(host(reported_url))
    return [
        {"id": "form_to_foreign_host", "value": bool(foreign),
         "kind": ("observed" if "observed" in kinds else "declared_in_script") if foreign else None,
         "detail": sorted({d["url"] for d in foreign})},
        {"id": "password_field", "value": bool(passwords), "kind": "observed" if passwords else None,
         "detail": sorted({p["page"] for p in passwords})},
        {"id": "redirect_count", "value": len(redirects), "kind": "observed" if redirects else None,
         "detail": [f"{r['type']}: {r['from']} -> {r['to']}" for r in redirects]},
        {"id": "final_host_differs", "value": final_host is not None and not same_site,
         "kind": "observed", "detail": final_host},
        {"id": "hidden_text_present", "value": bool(hidden), "kind": "observed" if hidden else None,
         "detail": [h["text"] for h in hidden]},
        {"id": "egress_blocked", "value": bool(blocked), "kind": "observed" if blocked else None,
         "detail": sorted({f"{e.get('host')}: {e.get('reason')}" for e in blocked})},
        {"id": "download_quarantined", "value": bool(downloads), "kind": "observed" if downloads else None,
         "detail": [d.get("suggested_filename") for d in downloads]},
    ]


def level(signals):
    values = {s["id"]: s["value"] for s in signals}
    if values["form_to_foreign_host"] and values["password_field"]:
        return PROVEN
    if values["form_to_foreign_host"]:
        return SUSPICIOUS
    return NOT_PROVEN
