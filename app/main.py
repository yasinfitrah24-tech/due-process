"""Web app: Queue and Case screens, JSON API, analyst approval."""
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app import compare, db, inventory, pipeline, policy, signals
from sandbox import launch

HERE = Path(__file__).parent
MAX_CLAIM_CHARS = 20000
DOCUMENTS = {"statement_of_reasons": "Statement of reasons", "customer_notice": "Customer notice",
             "reporter_reply": "Reporter reply"}
NEEDS_ENFORCEMENT = ("statement_of_reasons", "customer_notice")

templates = Jinja2Templates(directory=HERE / "templates")
executor = ThreadPoolExecutor(max_workers=launch.MAX_PARALLEL)


def submit(case_id):
    executor.submit(pipeline.process, case_id)


@asynccontextmanager
async def lifespan(_):
    db.init()
    db.fail_interrupted()
    launch.ensure_image()
    yield


app = FastAPI(title="Due Process", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


class Rejected(Exception):
    def __init__(self, message):
        self.message = message


class ReportIn(BaseModel):
    url: str = Field(max_length=2000)
    claim: str = Field(max_length=MAX_CLAIM_CHARS)


class ActionIn(BaseModel):
    action: str
    analyst: str = Field(default="analyst", min_length=1, max_length=100)


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


def apply_action(case_id, action, analyst):
    """Returns the case, or raises with the status code for why the action is refused."""
    case = db.get_case(case_id)
    if case is None:
        raise HTTPException(404, "case not found")
    if case["status"] not in ("ready", "decided"):
        raise HTTPException(409, "case has no verdict yet")
    allowed = policy.allowed_actions(case["level"])
    if action not in allowed:
        db.audit(case_id, analyst, "action_rejected", action=action, level=case["level"], allowed=allowed)
        raise HTTPException(403, f"{action!r} is not allowed at level {case['level']}")
    if not db.decide(case_id, action, analyst):
        db.audit(case_id, analyst, "action_rejected", action=action, reason="already decided")
        raise HTTPException(409, "case already decided")
    db.audit(case_id, analyst, "action_taken", action=action, level=case["level"],
             target=case["customer_id"], simulated=True)
    return db.get_case(case_id)


def sent_documents(case_id):
    return {a["detail"]["document"] for a in db.audit_log(case_id) if a["event"] == "document_sent"}


def send_document(case_id, name, analyst):
    """A draft leaves only by an analyst's hand, after the decision. Sending is simulated."""
    case = db.get_case(case_id)
    if case is None or name not in DOCUMENTS:
        raise HTTPException(404, "no such document")
    draft = db.model_outputs(case_id).get("documents")
    if not draft or draft["status"] != "accepted" or not draft["output"][name]:
        raise HTTPException(409, "no accepted draft of this document")
    if not case["decided_action"]:
        raise HTTPException(409, "decide the case first")
    if name in NEEDS_ENFORCEMENT and case["decided_action"] == "Close":
        db.audit(case_id, analyst, "document_rejected", document=name, action=case["decided_action"])
        raise HTTPException(403, f"{name} is not sent when the case is closed")
    if name in sent_documents(case_id):
        raise HTTPException(409, "already sent")
    db.audit(case_id, analyst, "document_sent", document=name, simulated=True)


def case_context(case_id):
    case = db.get_case(case_id)
    if case is None:
        raise HTTPException(404, "case not found")
    evidence = pipeline.load_evidence(case_id)
    for d in evidence.get("destinations", []):
        d["foreign"] = inventory.is_foreign(signals.host(d["page"]), signals.host(d["url"]))
    outputs = db.model_outputs(case_id)
    claim, vision = outputs.get("claim_extraction"), outputs.get("vision_claim")
    return {"case": case, "evidence": evidence, "audit": db.audit_log(case_id), "outputs": outputs,
            "comparison": compare.rows(claim and claim["output"], case["signals"], evidence, vision and vision["output"]),
            "usage": db.case_usage(case_id), "documents": DOCUMENTS, "sent": sent_documents(case_id),
            "allowed": policy.allowed_actions(case["level"]) if case["status"] == "ready" else ()}


@app.get("/")
def queue(request: Request):
    return templates.TemplateResponse(request, "queue.html", {"cases": db.list_cases(), "error": None})


@app.get("/partials/queue")
def queue_rows(request: Request):
    return templates.TemplateResponse(request, "_queue_rows.html", {"cases": db.list_cases()})


@app.post("/reports")
def report_form(request: Request, url: str = Form(max_length=2000), claim: str = Form(max_length=MAX_CLAIM_CHARS)):
    try:
        case_id = create_report(url, claim)
    except Rejected as e:
        return templates.TemplateResponse(request, "queue.html", {"cases": db.list_cases(), "error": e.message},
                                           status_code=422)
    return RedirectResponse(f"/cases/{case_id}", status_code=303)


@app.get("/cases/{case_id}")
def case_page(request: Request, case_id: int):
    return templates.TemplateResponse(request, "case.html", case_context(case_id))


@app.get("/partials/cases/{case_id}")
def case_partial(request: Request, case_id: int):
    return templates.TemplateResponse(request, "_case_body.html", case_context(case_id))


@app.get("/cases/{case_id}/shots/{name}")
def screenshot(case_id: int, name: str):
    path = db.case_dir(case_id) / name
    if not re.fullmatch(r"shot-\d+\.jpg", name) or not path.is_file():
        raise HTTPException(404, "no such screenshot")
    return FileResponse(path, media_type="image/jpeg")


@app.post("/cases/{case_id}/actions")
def action_form(case_id: int, action: str = Form(), analyst: str = Form(default="analyst", max_length=100)):
    apply_action(case_id, action, analyst)
    return RedirectResponse(f"/cases/{case_id}", status_code=303)


@app.post("/cases/{case_id}/documents/{name}/send")
def document_form(case_id: int, name: str, analyst: str = Form(default="analyst", max_length=100)):
    send_document(case_id, name, analyst)
    return RedirectResponse(f"/cases/{case_id}", status_code=303)


@app.post("/api/reports", status_code=202)
def report_api(report: ReportIn):
    try:
        case_id = create_report(report.url, report.claim)
    except Rejected as e:
        return JSONResponse({"error": e.message}, status_code=422)
    return {"case_id": case_id, "customer_id": db.get_case(case_id)["customer_id"]}


@app.get("/api/cases/{case_id}")
def case_api(case_id: int):
    ctx = case_context(case_id)
    return {**ctx["case"], "allowed_actions": ctx["allowed"], "audit": ctx["audit"], "outputs": ctx["outputs"],
            "comparison": ctx["comparison"], "usage": ctx["usage"], "sent": sorted(ctx["sent"])}


@app.post("/api/cases/{case_id}/actions")
def action_api(case_id: int, body: ActionIn):
    case = apply_action(case_id, body.action, body.analyst)
    return {"case_id": case_id, "action": case["decided_action"], "target": case["customer_id"]}
