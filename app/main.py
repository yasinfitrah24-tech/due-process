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
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import appeals, compare, db, explain, inventory, letters, pipeline, policy, ratelimit, signals, timeline, visitor
from app.examples import EXAMPLES, FIXABLE_EXAMPLE
from app.twins import TWINS

HERE = Path(__file__).parent
MAX_CLAIM_CHARS = 20000
DOCUMENTS = {"statement_of_reasons": "Statement of reasons", "customer_notice": "Customer notice",
             "reporter_reply": "Reporter reply"}
NEEDS_ENFORCEMENT = ("statement_of_reasons", "customer_notice")
ACTION_LABELS = {"Suspend": "Suspend customer", "Warn": "Warn customer", "Close": "Close report"}
SHOWCASE_TWIN = "login"

templates = Jinja2Templates(directory=HERE / "templates")
templates.env.globals["nonce"] = lambda: secrets.token_urlsafe(16)
executor = ThreadPoolExecutor(max_workers=visitor.MAX_PARALLEL)


def submit(case_id):
    executor.submit(pipeline.process, case_id)


def submit_appeal(appeal_id):
    executor.submit(pipeline.revisit, appeal_id)


def submit_baseline(token):
    executor.submit(pipeline.run_baseline, token)


@asynccontextmanager
async def lifespan(_):
    db.init()
    db.fail_interrupted()
    yield


app = FastAPI(title="Due Process", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.exception_handler(StarletteHTTPException)
async def http_error(request, error):
    if not wants_page(request):
        return await http_exception_handler(request, error)
    return error_page(request, error.status_code, FOUND_NOTHING.get(error.detail, str(error.detail)))


@app.exception_handler(RequestValidationError)
async def form_error(request, error):
    if not wants_page(request):
        return await request_validation_exception_handler(request, error)
    return error_page(request, 422, "That form was incomplete or not valid.")


@app.exception_handler(Exception)
async def server_error(request, error):
    if not wants_page(request):
        return JSONResponse({"detail": "Internal Server Error"}, status_code=500)
    return error_page(request, 500, "Something went wrong on our side.")


FOUND_NOTHING = {"Not Found": "We could not find that page.", "case not found": "We could not find that case.",
                 "no such appeal link": "This appeal link does not exist.",
                 "no such comparison": "This comparison does not exist."}
NEXT_STEP = {403: "Go back and choose one of the actions shown there.",
             404: "Check the link, or go back and start from there.",
             405: "Go back and use the buttons on the page.",
             409: "Go back: the page shows where this stands now.",
             422: "Go back, check what you entered and send it again.",
             429: "Wait ten minutes, then try again.",
             500: "Nothing was lost. Go back and try again in a moment.",
             503: "Wait a few minutes, then try again."}


def wants_page(request):
    """A person in a browser gets a page. The API, polling and anything else that asks for data get what they had."""
    path = request.url.path
    return "text/html" in request.headers.get("accept", "") and not path.startswith(("/api/", "/partials/"))


def error_page(request, status, what):
    """One sentence on what happened, one on what to do, and a way back. Only this app's own paths are linked."""
    match = re.match(r"/(cases|appeal)/([\w-]+)", request.url.path) if request.method == "POST" else None
    if match:
        back, label = f"/{match[1]}/{match[2]}", "Back to the case"
    elif request.url.path.startswith("/compare"):
        back, label = "/compare", "Back to One report, two pages"
    else:
        back, label = "/", "Back to the queue"
    sentence = what[0].upper() + what[1:]
    return templates.TemplateResponse(request, "error.html", {
        "status": status, "what": sentence if sentence.endswith(".") else f"{sentence}.",
        "next_step": NEXT_STEP.get(status, NEXT_STEP[409]), "back": back, "back_label": label}, status_code=status)


def once(nonce, work):
    """Every form carries a nonce, spent by its first request. A repeat starts nothing: it follows the first one to where
    it ended. Without a nonce, as from the API, this does nothing and the rate limits apply. A request that fails gives
    the nonce back, so the same form can be sent again."""
    if nonce and not db.claim_submission(nonce):
        location = db.wait_for_submission(nonce)
        return once(nonce, work) if location is None else RedirectResponse(location, status_code=303)
    try:
        location = work()
    except Exception:
        if nonce:
            db.release_submission(nonce)
        raise
    if nonce:
        db.finish_submission(nonce, location)
    return RedirectResponse(location, status_code=303)


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
             sent_text=letters.sent_text(draft["output"][name], case["level"], case["decided_action"], appeal_url,
                                         explain.why(case["signals"])))


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
                      "not_reproduced": appeals.lower_level(case, a),
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
    allowed = policy.allowed_for(case) if case["status"] == "ready" else ()
    return {"case": case, "evidence": evidence, "audit": db.audit_log(case_id), "outputs": outputs, "appeals": views,
            "polling": polling, "choices": appeals.CHOICES, "timeline": timeline.rows(db.case_events(case_id)),
            "why": explain.why(case["signals"]), "present": explain.present(case["signals"]),
            "contained": explain.contained(evidence, case["receipt"]), "status_line": explain.status_line(case),
            "live_line": explain.live_line(case),
            "action_labels": ACTION_LABELS, "appealable": bool(set(allowed) & set(policy.APPEALABLE)),
            "comparison": compare.rows(claim and claim["output"], case["signals"], evidence, vision and vision["output"]),
            "usage": db.case_usage(case_id), "documents": DOCUMENTS, "sent": sent_documents(case_id),
            "allowed": allowed}


def baseline_is_wrong(level, action):
    return level == "Not proven" and action in policy.APPEALABLE


def showcase():
    """The front page's report and, once one has run, what a real comparison of it found. Nothing here is made up."""
    twin = TWINS[SHOWCASE_TWIN]
    comparison = db.latest_comparison(SHOWCASE_TWIN)
    sides = []
    for side in ("abuse", "legit") if comparison else ():
        case = db.get_case(comparison[f"{side}_case"])
        action = comparison[f"{side}_baseline"]["action"]
        if case["status"] != "ready" or case["note"]:
            return {"report": twin["report"], "sides": []}
        sides.append({"url": case["url"].split("://", 1)[1], "level": case["level"], "why": explain.why(case["signals"]),
                      "baseline": action, "wrong": baseline_is_wrong(case["level"], action)})
    return {"report": twin["report"], "sides": sides}


def rejected_next(error):
    if error == "not our customer":
        return "Use one of the hosts listed below, or try the address under Test any URL."
    return "Check the address and send it again."


def front_page(request, error=None, status_code=200, form="report", values=None):
    hosts = [h for c in inventory.CUSTOMERS if not c.get("eval_only") for h in c["hosts"]]
    return templates.TemplateResponse(request, "queue.html", {"cases": db.list_cases(), "error": error, "hosts": hosts,
                                                              "examples": EXAMPLES, "fixable": FIXABLE_EXAMPLE,
                                                              "showcase": showcase(), "form": form,
                                                              "values": values or {},
                                                              "error_next": rejected_next(error)},
                                      status_code=status_code)


@app.get("/")
def queue(request: Request):
    return front_page(request)


@app.post("/try/{key}")
def try_example(request: Request, key: str, nonce: str = Form(default="", max_length=64)):
    if key != "fixable" and key not in EXAMPLES:
        raise HTTPException(404, "no such example")

    def work():
        guard(request)
        if key == "fixable":  # each try gets its own page, so one visitor's fix does not touch another's case
            case_id = create_report(f"http://{appeals.FIXABLE_HOST}/shop/{secrets.token_hex(8)}", FIXABLE_EXAMPLE["claim"])
        else:
            case_id = create_report(EXAMPLES[key]["url"], EXAMPLES[key]["claim"])
        return f"/cases/{case_id}"
    return once(nonce, work)


def compare_guard(request):
    if not ratelimit.allow(f"compare:{client_address(request)}", limit=ratelimit.COMPARES_PER_WINDOW):
        raise HTTPException(429, "too many comparisons from this address, try again later")
    if db.waiting_cases() >= ratelimit.MAX_WAITING:
        raise HTTPException(503, "the queue is full, try again later")


def twin_case(url, report):
    host = inventory.reported_host(url)
    return db.create_case(url, host, report, inventory.customer_for(host), mode="twin")


@app.get("/compare")
def compare_page(request: Request):
    return templates.TemplateResponse(request, "compare.html", {"twins": TWINS, "error": None})


@app.post("/compare/run/{key}")
def compare_run(request: Request, key: str, nonce: str = Form(default="", max_length=64)):
    if key not in TWINS:
        raise HTTPException(404, "no such pair")

    def work():
        compare_guard(request)
        twin = TWINS[key]
        abuse_case, legit_case = twin_case(twin["abuse_url"], twin["report"]), twin_case(twin["legit_url"], twin["report"])
        token = db.create_comparison(key, abuse_case, legit_case)
        for case_id in (abuse_case, legit_case):
            db.audit(case_id, "public", "comparison_started", twin=key)
            submit(case_id)
        submit_baseline(token)
        return f"/compare/{token}"
    return once(nonce, work)


def compare_context(token):
    comparison = db.get_comparison(token)
    if comparison is None:
        raise HTTPException(404, "no such comparison")
    twin = TWINS[comparison["twin"]]
    sides = []
    for side in ("abuse", "legit"):
        case = db.get_case(comparison[f"{side}_case"])
        steps = timeline.rows(db.case_events(case["id"]))
        baseline = comparison[f"{side}_baseline"]
        sides.append({"label": twin[f"{side}_label"], "case": case, "baseline": baseline,
                      "present": [s["id"] for s in case["signals"] or [] if s["value"]],
                      "step": steps[-1]["text"] if steps and case["status"] != "ready" else None,
                      "why": explain.why(case["signals"]), "host": case["url"].split("://", 1)[1],
                      "allowed": policy.ALLOWED_ACTIONS.get(case["level"], ()),
                      "wrong": bool(baseline) and baseline_is_wrong(case["level"], baseline["action"])})
    polling = any(s["case"]["status"] != "ready" or not s["baseline"] for s in sides)
    levels = " ".join(f"{s['label']}: {s['case']['level']}." for s in sides if s["case"]["level"])
    status_line = " ".join(filter(None, [levels, "" if not polling else "Opening both pages and asking the baseline."]))
    return {"comparison": comparison, "twin": twin, "sides": sides, "polling": polling, "status_line": status_line}


@app.get("/compare/{token}")
def compare_result(request: Request, token: str):
    return templates.TemplateResponse(request, "compare_result.html", compare_context(token))


@app.get("/partials/compare/{token}")
def compare_partial(request: Request, token: str):
    return templates.TemplateResponse(request, "_compare_body.html", compare_context(token))


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
    closed = appeals.closed_reason(case, views)
    return {"case": case, "appeals": views, "choices": appeals.CHOICES, "closed": closed,
            "polling": any(a["status"] in appeals.PENDING for a in views), "slug": appeals.fixable_slug(case),
            "why": explain.why(case["signals"]), "headline": explain.customer_headline(case, views, closed),
            "steps": explain.appeal_steps(case, views), "status_line": explain.appeal_status(views),
            "fixed": fix_simulated(case["id"]),
            "notice": letters.clean(notice) if notice else None,
            "action_text": letters.outcome_sentence(case["level"], case["decided_action"])}


def fix_simulated(case_id):
    return any(a["event"] == "customer_fix_simulated" for a in db.audit_log(case_id))


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
                text: str = Form(default="", max_length=appeals.MAX_TEXT_CHARS),
                nonce: str = Form(default="", max_length=64)):
    """Takes a choice and text. Neither is read by anything that decides; the second visit is what counts."""
    case = find_appealed_case(token)
    if choice not in appeals.CHOICES:
        raise HTTPException(422, "unknown choice")

    def work():
        appeal_guard(request)
        reason = appeals.closed_reason(case, db.case_appeals(case["id"]))
        if reason:
            db.audit(case["id"], "customer", "appeal_refused", reason=reason)
            raise HTTPException(409, reason)
        appeal_id = db.create_appeal(case["id"], choice, text)
        db.audit(case["id"], "customer", "appeal_received", appeal_id=appeal_id, choice=choice)
        submit_appeal(appeal_id)
        return f"/appeal/{token}"
    return once(nonce, work)


@app.post("/appeal/{token}/simulate-fix")
def simulate_fix(request: Request, token: str, nonce: str = Form(default="", max_length=64)):
    case = find_appealed_case(token)
    slug = appeals.fixable_slug(case)
    if slug is None:
        raise HTTPException(404, "this page cannot be changed")

    def work():
        if not fix_simulated(case["id"]):
            appeal_guard(request)
            visitor.simulate_fix(slug)
            db.audit(case["id"], "customer", "customer_fix_simulated", url=case["url"])
        return f"/appeal/{token}"
    return once(nonce, work)


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
def appeal_decide_form(ref: str, appeal_id: int, decision: str = Form(), analyst: str = Form(default="analyst", max_length=100),
                       nonce: str = Form(default="", max_length=64)):
    def work():
        decide_appeal(ref, appeal_id, decision, analyst)
        return f"/cases/{ref}"
    return once(nonce, work)


@app.post("/cases/{ref}/appeals/{appeal_id}/send")
def appeal_send_form(ref: str, appeal_id: int, analyst: str = Form(default="analyst", max_length=100),
                     nonce: str = Form(default="", max_length=64)):
    def work():
        send_appeal_letter(ref, appeal_id, analyst)
        return f"/cases/{ref}"
    return once(nonce, work)


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
def report_form(request: Request, url: str = Form(max_length=2000), claim: str = Form(max_length=MAX_CLAIM_CHARS),
                nonce: str = Form(default="", max_length=64)):
    def work():
        guard(request)
        return f"/cases/{create_report(url, claim)}"
    try:
        return once(nonce, work)
    except Rejected as e:
        return front_page(request, e.message, 422, values={"url": url, "claim": claim})


@app.post("/test-url")
def test_url_form(request: Request, url: str = Form(max_length=2000), nonce: str = Form(default="", max_length=64)):
    def work():
        guard(request)
        return f"/cases/{create_test(url)}"
    try:
        return once(nonce, work)
    except Rejected as e:
        return front_page(request, e.message, 422, form="test", values={"test_url": url})


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
def action_form(ref: str, action: str = Form(), analyst: str = Form(default="analyst", max_length=100),
                nonce: str = Form(default="", max_length=64)):
    def work():
        apply_action(ref, action, analyst)
        return f"/cases/{ref}"
    return once(nonce, work)


@app.post("/cases/{ref}/documents/{name}/send")
def document_form(request: Request, ref: str, name: str, analyst: str = Form(default="analyst", max_length=100),
                  nonce: str = Form(default="", max_length=64)):
    def work():
        send_document(request, ref, name, analyst)
        return f"/cases/{ref}"
    return once(nonce, work)


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
            "comparison": ctx["comparison"], "usage": ctx["usage"], "sent": ctx["sent"], "appeals": ctx["appeals"],
            "timeline": ctx["timeline"]}


@app.post("/api/cases/{ref}/actions")
def action_api(ref: str, body: ActionIn):
    case = apply_action(ref, body.action, body.analyst)
    return {"case_id": case["id"], "action": case["decided_action"], "target": case["customer_id"]}
