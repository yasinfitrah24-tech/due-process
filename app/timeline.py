"""Turns recorded events into lines for the Case screen. Every line is something that happened; nothing is estimated."""

TEXT = {
    "sandbox_requested": "Asked the launcher for a new sandbox",
    "container_started": "Container started: {container_id}",
    "visit_started": "Browser started",
    "navigation": "Browser opened {url}",
    "http_redirect": "HTTP redirect from {from} to {to}",
    "page_loaded": "Page loaded: {url}",
    "download": "Page started a download: {name}. Held in the sandbox, not opened",
    "visit_finished": "Browser finished",
    "egress_allowed": "Proxy allowed {method} {host}:{port}",
    "egress_blocked": "Proxy blocked {method} {host}:{port}: {reason}",
    "egress_failed": "Proxy could not connect to {host}:{port}",
    "container_gone": "Container gone: {container_id}",
    "container_not_gone": "Container still present: {container_id}",
    "level_computed": "Level computed by code: {level}",
    "model_started": "Model call started: {role}",
    "model_finished": "Model call finished: {role}, {status}",
}
PROXY_KINDS = {"allowed": "egress_allowed", "blocked": "egress_blocked", "failed": "egress_failed"}


def describe(event):
    template = TEXT.get(event["kind"], event["kind"])
    try:
        return template.format(**event["detail"])
    except (KeyError, IndexError):
        return template


def rows(events):
    """[{"at", "source", "text", "visit"}], in time order, seconds from the first event of that visit."""
    start = {}
    out = []
    for e in sorted(events, key=lambda x: (x["ts"], x["id"])):
        visit = e["appeal_id"]
        start.setdefault(visit, e["ts"])
        out.append({"at": round(e["ts"] - start[visit], 1), "source": e["source"], "text": describe(e),
                    "visit": "second visit" if visit else "first visit", "blocked": e["kind"] == "egress_blocked"})
    return out
