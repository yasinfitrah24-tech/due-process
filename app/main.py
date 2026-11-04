"""Web app: Queue and Case screens, JSON API, analyst approval, customer appeals."""
import json
import os
import re
import secrets
import socket
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app import appeals, compare, db, inventory, letters, pipeline, policy, ratelimit, signals, visitor
from app.examples import EXAMPLES, FIXABLE_EXAMPLE

HERE = Path(__file__).parent
MAX_CLAIM_CHARS = 20000
DOCUMENTS = {"statement_of_reasons": "Statement of reasons", "customer_notice": "Customer notice",
             "reporter_reply": "Reporter reply"}
NEEDS_ENFORCEMENT = ("statement_of_reasons", "customer_notice")

templates = Jinja2Templates(directory=HERE / "templates")
executor = ThreadPoolExecutor(max_workers=visitor.MAX_PARALLEL)


def submit(case_id):
    executor.submit(pipeline.process, case_id)


def submit_appeal(appeal_id):
    executor.submit(pipeline.revisit, appeal_id)


@asynccontextmanager
async def lifespan(_):
    db.init()
    db.fail_interrupted()
    yield


app = FastAPI(title="Due Process", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


class Rejected(Exception):
    def __init__(self, message):
        self.message = message


class ReportIn(BaseModel):
    url: str = Field(max_length=2000)
    claim: str = Field(max_length=MAX_CLAIM_CHARS)


class TestUrlIn(BaseModel):
    url: str = Field(max_length=2000)


class ActionIn(BaseModel):
    action: str
    analyst: str = Field(default="analyst", min_length=1, max_length=100)


def trusted_proxy_addresses():
    host = os.environ.get("TRUSTED_PROXY_HOST")
    try:
        return {info[4][0] for info in socket.getaddrinfo(host, None)} if host else set()
    except OSError:
        return set()


def client_address(request):
    """The socket address, unless the socket is Caddy's: then the address Caddy reports. Caddy replaces this header
    with the real client, and anyone else's copy of it is ignored."""
    peer = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and peer in trusted_proxy_addresses():
        return forwarded.split(",")[-1].strip()
    return peer


def guard(request):
    """Limits for anything public that starts a sandbox."""
    if not ratelimit.allow(client_address(request)):
        raise HTTPException(429, "too many reports from this address, try again later")
    if db.waiting_cases() >= ratelimit.MAX_WAITING:
        raise HTTPException(503, "the queue is full, try again later")


def create_test(url):
    """Test any URL: evidence and a verdict only. No customer, so no target and no action."""
    try:
        host = inventory.reported_host(url)
    except inventory.BadUrl as e:
        raise Rejected(str(e)) from None
    case_id = db.create_case(url, host, "", {"id": "", "name": ""}, mode="test")
    db.audit(case_id, "public", "test_url_received", url=url)
    submit(case_id)
    return db.get_case(case_id)["ref"]


def create_report(url, claim):
    """The customer comes from the URL's host. Nothing in the claim is read here."""
    try:
        host = inventory.reported_host(url)
    except inventory.BadUrl as e:
        db.audit(None, "system", "report_rejected", url=url, reason=str(e))
        raise Rejected(str(e)) from None
    customer = inventory.customer_for(host)
    if customer is None:
        db.audit(None, "system", "report_rejected", url=url, reason="not our customer")
        raise Rejected("not our customer")
    case_id = db.create_case(url, host, claim, customer)
    db.audit(case_id, "reporter", "report_received", url=url, customer_id=customer["id"])
    submit(case_id)
    return case_id


def find_case(ref):
    case = db.get_case_by_ref(ref)
    if case is None:
        raise HTTPException(404, "case not found")
    return case


def apply_action(ref, action, analyst):
    """Returns the case, or raises with the status code for why the action is refused."""
    case = find_case(ref)
    case_id = case["id"]
    if case["status"] not in ("ready", "decided"):
        raise HTTPException(409, "case has no verdict yet")
    allowed = policy.allowed_for(case)
    if action not in allowed:
        db.audit(case_id, analyst, "action_rejected", action=action, level=case["level"], allowed=allowed)
        raise HTTPException(403, f"{action!r} is not allowed at level {case['level']}")
    if not db.decide(case_id, action, analyst):
        db.audit(case_id, analyst, "action_rejected", action=action, reason="already decided")
        raise HTTPException(409, "case already decided")
    db.audit(case_id, analyst, "action_taken", action=action, level=case["level"],
             target=case["customer_id"], simulated=True)
    appeals.new_token_if_needed(case_id, action)
    return db.get_case(case_id)


def sent_documents(case_id):
    """{document: text as sent}."""
    return {a["detail"]["document"]: a["detail"]["sent_text"] for a in db.audit_log(case_id)
            if a["event"] == "document_sent"}


def public_base(request):
    return os.environ.get("PUBLIC_URL") or str(request.base_url).rstrip("/")


def send_document(request, ref, name, analyst):
    """A draft leaves only by an analyst's hand, after the decision. Sending is simulated."""
    case = find_case(ref)
    case_id = case["id"]
    if name not in DOCUMENTS:
        raise HTTPException(404, "no such document")
    draft = db.model_outputs(case_id).get("documents")
    if not draft or draft["status"] not in ("accepted", "template") or not draft["output"][name]:
        raise HTTPException(409, "no draft of this document")
    if not case["decided_action"]:
        raise HTTPException(409, "decide the case first")
    if name in NEEDS_ENFORCEMENT and case["decided_action"] == "Close":
        db.audit(case_id, analyst, "document_rejected", document=name, action=case["decided_action"])
        raise HTTPException(403, f"{name} is not sent when the case is closed")
    if name in sent_documents(case_id):
        raise HTTPException(409, "already sent")
    appeal_url = None
    if name == "customer_notice" and case["appeal_token"]:
        appeal_url = f"{public_base(request)}/appeal/{case['appeal_token']}"
    # Both versions are kept: the cited draft for the record, the plain text as the reader got it.
    db.audit(case_id, analyst, "document_sent", document=name, simulated=True, cited_draft=draft["output"][name],
             sent_text=letters.sent_text(draft["output"][name], case["level"], case["decided_action"], appeal_url))


def sent_appeal_letters(case_id):
    return {a["detail"]["appeal_id"]: a["detail"]["sent_text"] for a in db.audit_log(case_id)
            if a["event"] == "appeal_letter_sent"}


def appeal_views(case, outputs):
    sent = sent_appeal_letters(case["id"])
    views = []
    for a in db.case_appeals(case["id"]):
        evidence_file = db.case_dir(case["id"]) / f"appeal-{a['id']}" / "evidence.json"
        pages = json.loads(evidence_file.read_text()).get("pages", []) if evidence_file.exists() else []
        shots = [p["screenshot"]["file"] for p in pages if (p.get("screenshot") or {}).get("file")]
        views.append({**a, "allowed": appeals.allowed(case, a), "rows": appeals.comparison(case, a),
                      "letter": outputs.get(f"appeal_letter:{a['id']}"), "sent": sent.get(a["id"]),
                      "shot": shots[-1] if shots else None})
    return views


def case_context(ref):
    case = find_case(ref)
    case_id = case["id"]
    evidence = pipeline.load_evidence(case_id)
    for d in evidence.get("destinations", []):
        d["foreign"] = inventory.is_foreign(signals.host(d["page"]), signals.host(d["url"]))
    outputs = db.model_outputs(case_id)
    claim, vision = outputs.get("claim_extraction"), outputs.get("vision_claim")
    views = appeal_views(case, outputs)
    polling = case["status"] in ("queued", "visiting", "drafting") or any(a["status"] in appeals.PENDING for a in views)
    return {"case": case, "evidence": evidence, "audit": db.audit_log(case_id), "outputs": outputs, "appeals": views,
            "polling": polling, "choices": appeals.CHOICES,
            "comparison": compare.rows(claim and claim["output"], case["signals"], evidence, vision and vision["output"]),
            "usage": db.case_usage(case_id), "documents": DOCUMENTS, "sent": sent_documents(case_id),
            "allowed": policy.allowed_for(case) if case["status"] == "ready" else ()}


def front_page(request, error=None, status_code=200):
    hosts = [h for c in inventory.CUSTOMERS if not c.get("eval_only") for h in c["hosts"]]
    return templates.TemplateResponse(request, "queue.html", {"cases": db.list_cases(), "error": error, "hosts": hosts,
                                                              "examples": EXAMPLES, "fixable": FIXABLE_EXAMPLE},
                                      status_code=status_code)


@app.get("/")
def queue(request: Request):
    return front_page(request)


@app.post("/try/{key}")
def try_example(request: Request, key: str):
    if key != "fixable" and key not in EXAMPLES:
        raise HTTPException(404, "no such example")
    guard(request)
    if key == "fixable":  # each try gets its own page, so one visitor's fix does not touch another's case
        case_id = create_report(f"http://{appeals.FIXABLE_HOST}/shop/{secrets.token_hex(8)}", FIXABLE_EXAMPLE["claim"])
    else:
        case_id = create_report(EXAMPLES[key]["url"], EXAMPLES[key]["claim"])
    return RedirectResponse(f"/cases/{case_id}", status_code=303)


def find_appealed_case(token):
    case = db.get_case_by_appeal_token(token)
    if case is None:
        raise HTTPException(404, "no such appeal link")
    return case


def customer_context(token):
    case = find_appealed_case(token)
    outputs = db.model_outputs(case["id"])
    views = appeal_views(case, outputs)
    draft = outputs.get("documents")
    notice = draft["output"]["customer_notice"] if draft and draft["output"] else None
    return {"case": case, "appeals": views, "choices": appeals.CHOICES, "closed": appeals.closed_reason(case, views),
            "polling": any(a["status"] in appeals.PENDING for a in views), "slug": appeals.fixable_slug(case),
            "notice": letters.clean(notice) if notice else None,
            "action_text": letters.outcome_sentence(case["level"], case["decided_action"])}


def appeal_guard(request):
    if not ratelimit.allow(f"appeal:{client_address(request)}", limit=ratelimit.APPEALS_PER_WINDOW):
        raise HTTPException(429, "too many appeal requests from this address, try again later")


@app.get("/appeal/{token}")
def customer_page(request: Request, token: str):
    return templates.TemplateResponse(request, "customer.html", customer_context(token))


@app.get("/partials/appeal/{token}")
def customer_partial(request: Request, token: str):
    return templates.TemplateResponse(request, "_customer_body.html", customer_context(token))


@app.post("/appeal/{token}")
def appeal_form(request: Request, token: str, choice: str = Form(),
                text: str = Form(default="", max_length=appeals.MAX_TEXT_CHARS)):
    """Takes a choice and text. Neither is read by anything that decides; the second visit is what counts."""
    case = find_appealed_case(token)
    if choice not in appeals.CHOICES:
        raise HTTPException(422, "unknown choice")
    appeal_guard(request)
    reason = appeals.closed_reason(case, db.case_appeals(case["id"]))
    if reason:
        db.audit(case["id"], "customer", "appeal_refused", reason=reason)
        raise HTTPException(409, reason)
    appeal_id = db.create_appeal(case["id"], choice, text)
    db.audit(case["id"], "customer", "appeal_received", appeal_id=appeal_id, choice=choice)
    submit_appeal(appeal_id)
    return RedirectResponse(f"/appeal/{token}", status_code=303)


@app.post("/appeal/{token}/simulate-fix")
def simulate_fix(request: Request, token: str):
    case = find_appealed_case(token)
    slug = appeals.fixable_slug(case)
    if slug is None:
        raise HTTPException(404, "this page cannot be changed")
    appeal_guard(request)
    visitor.simulate_fix(slug)
    db.audit(case["id"], "customer", "customer_fix_simulated", url=case["url"])
    return RedirectResponse(f"/appeal/{token}", status_code=303)


def find_appeal(ref, appeal_id):
    case = find_case(ref)
    appeal = db.get_appeal(appeal_id)
    if appeal is None or appeal["case_id"] != case["id"]:
        raise HTTPException(404, "no such appeal")
    return case, appeal


def decide_appeal(ref, appeal_id, decision, analyst):
    """Only an analyst, and Reinstate only if the second visit came out lower than the level at the action."""
    case, appeal = find_appeal(ref, appeal_id)
    if appeal["status"] not in ("ready", "decided"):
        raise HTTPException(409, "the second visit is not finished")
    allowed = appeals.allowed(case, appeal)
    if appeal["status"] == "ready" and decision not in allowed:
        db.audit(case["id"], analyst, "appeal_decision_rejected", appeal_id=appeal_id, decision=decision,
                 level_at_action=case["level"], revisit_level=appeal["level"], allowed=allowed)
        raise HTTPException(403, f"{decision!r} is not allowed: level at action {case['level']}, "
                                 f"second visit {appeal['level']}")
    if not db.decide_appeal(appeal_id, decision, analyst):
        db.audit(case["id"], analyst, "appeal_decision_rejected", appeal_id=appeal_id, reason="already decided")
        raise HTTPException(409, "appeal already decided")
    db.audit(case["id"], analyst, "appeal_decided", appeal_id=appeal_id, decision=decision, level_at_action=case["level"],
             revisit_level=appeal["level"], target=case["customer_id"], simulated=True)
    return decision


def send_appeal_letter(ref, appeal_id, analyst):
    case, appeal = find_appeal(ref, appeal_id)
    if not appeal["decision"]:
        raise HTTPException(409, "decide the appeal first")
    letter = db.model_outputs(case["id"]).get(f"appeal_letter:{appeal_id}")
    if not letter or letter["status"] not in ("accepted", "template"):
        raise HTTPException(409, "no draft of this letter")
    if appeal_id in sent_appeal_letters(case["id"]):
        raise HTTPException(409, "already sent")
    sentences = letter["output"]["sentences"]
    db.audit(case["id"], analyst, "appeal_letter_sent", appeal_id=appeal_id, simulated=True, cited_draft=sentences,
             sent_text=letters.appeal_sent_text(sentences, appeal["level"], appeal["decision"]))


@app.post("/cases/{ref}/appeals/{appeal_id}/decide")
def appeal_decide_form(ref: str, appeal_id: int, decision: str = Form(), analyst: str = Form(default="analyst", max_length=100)):
    decide_appeal(ref, appeal_id, decision, analyst)
    return RedirectResponse(f"/cases/{ref}", status_code=303)


@app.post("/cases/{ref}/appeals/{appeal_id}/send")
def appeal_send_form(ref: str, appeal_id: int, analyst: str = Form(default="analyst", max_length=100)):
    send_appeal_letter(ref, appeal_id, analyst)
    return RedirectResponse(f"/cases/{ref}", status_code=303)


@app.get("/cases/{ref}/appeals/{appeal_id}/shots/{name}")
def appeal_screenshot(ref: str, appeal_id: int, name: str):
    case, _ = find_appeal(ref, appeal_id)
    path = db.case_dir(case["id"]) / f"appeal-{appeal_id}" / name
    if not re.fullmatch(r"shot-\d+\.jpg", name) or not path.is_file():
        raise HTTPException(404, "no such screenshot")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/partials/queue")
def queue_rows(request: Request):
    return templates.TemplateResponse(request, "_queue_rows.html", {"cases": db.list_cases()})


@app.post("/reports")
def report_form(request: Request, url: str = Form(max_length=2000), claim: str = Form(max_length=MAX_CLAIM_CHARS)):
    guard(request)
    try:
        case_id = create_report(url, claim)
    except Rejected as e:
        return front_page(request, e.message, 422)
    return RedirectResponse(f"/cases/{case_id}", status_code=303)


@app.post("/test-url")
def test_url_form(request: Request, url: str = Form(max_length=2000)):
    guard(request)
    try:
        ref = create_test(url)
    except Rejected as e:
        return front_page(request, e.message, 422)
    return RedirectResponse(f"/cases/{ref}", status_code=303)


@app.get("/cases/{ref}")
def case_page(request: Request, ref: str):
    return templates.TemplateResponse(request, "case.html", case_context(ref))


@app.get("/partials/cases/{ref}")
def case_partial(request: Request, ref: str):
    return templates.TemplateResponse(request, "_case_body.html", case_context(ref))


@app.get("/cases/{ref}/shots/{name}")
def screenshot(ref: str, name: str):
    path = db.case_dir(find_case(ref)["id"]) / name
    if not re.fullmatch(r"shot-\d+\.jpg", name) or not path.is_file():
        raise HTTPException(404, "no such screenshot")
    return FileResponse(path, media_type="image/jpeg")


@app.post("/cases/{ref}/actions")
def action_form(ref: str, action: str = Form(), analyst: str = Form(default="analyst", max_length=100)):
    apply_action(ref, action, analyst)
    return RedirectResponse(f"/cases/{ref}", status_code=303)


@app.post("/cases/{ref}/documents/{name}/send")
def document_form(request: Request, ref: str, name: str, analyst: str = Form(default="analyst", max_length=100)):
    send_document(request, ref, name, analyst)
    return RedirectResponse(f"/cases/{ref}", status_code=303)


@app.post("/api/test-url", status_code=202)
def test_url_api(request: Request, body: TestUrlIn):
    guard(request)
    try:
        ref = create_test(body.url)
        return {"ref": ref, "case_url": f"/cases/{ref}"}
    except Rejected as e:
        return JSONResponse({"error": e.message}, status_code=422)


@app.post("/api/reports", status_code=202)
def report_api(request: Request, report: ReportIn):
    guard(request)
    try:
        case_id = create_report(report.url, report.claim)
    except Rejected as e:
        return JSONResponse({"error": e.message}, status_code=422)
    return {"case_id": case_id, "customer_id": db.get_case(case_id)["customer_id"]}


@app.get("/api/cases/{ref}")
def case_api(ref: str):
    ctx = case_context(ref)
    return {**ctx["case"], "allowed_actions": ctx["allowed"], "audit": ctx["audit"], "outputs": ctx["outputs"],
            "comparison": ctx["comparison"], "usage": ctx["usage"], "sent": ctx["sent"], "appeals": ctx["appeals"]}


@app.post("/api/cases/{ref}/actions")
def action_api(ref: str, body: ActionIn):
    case = apply_action(ref, body.action, body.analyst)
    return {"case_id": case["id"], "action": case["decided_action"], "target": case["customer_id"]}
