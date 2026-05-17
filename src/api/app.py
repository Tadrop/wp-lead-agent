"""FastAPI app — webhook receiver + admin dashboard + health.

Webhook URL shape:
    POST /webhook/{client_id}/{plugin}

The webhook receiver:
  1. Loads the client config (fails 404 if unknown)
  2. Verifies the HMAC signature (header `X-Webhook-Signature`)
  3. Picks the adapter for `{plugin}` and normalizes the payload to a Lead
  4. Audits, indexes the lead, RPUSHes onto the queue
  5. Returns 202 Accepted

The dashboard is read-only (HTML) — per-client lead list + per-lead audit
trail. Everything is rendered server-side via Jinja2.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi import Path as PathParam
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..adapters.forms import UnknownPluginError, get_form_adapter, registered_form_plugins
from ..audit import get_audit_trail
from ..audit.writer import audit_step, hash_payload
from ..config_loader import ClientConfigError, load_client_config
from ..logging_setup import configure_logging, get_logger
from ..models import AuditStatus, Lead, LeadStatus
from ..queue import get_queue
from ..settings import get_settings
from .security import verify_signature

log = get_logger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def build_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Universal WordPress Agent",
        version="0.1.0",
        description="One agent platform for WordPress forms → CRM → email.",
    )

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "form_plugins": registered_form_plugins(),
            "queue": get_queue().queue_lengths(),
        }

    # ---- Webhook receiver ------------------------------------------------

    @app.post("/webhook/{client_id}/{plugin}")
    async def webhook(
        request: Request,
        client_id: str = PathParam(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$"),
        plugin: str = PathParam(..., min_length=1, max_length=32, pattern=r"^[a-z0-9_]+$"),
        x_webhook_signature: str | None = Header(default=None, alias="X-Webhook-Signature"),
    ):
        body = await request.body()

        # 1. Resolve config
        try:
            config = load_client_config(client_id)
        except ClientConfigError as e:
            log.warning("webhook_unknown_client", client_id=client_id, error=str(e))
            raise HTTPException(status_code=404, detail=f"unknown client: {client_id}") from e

        # 2. Verify signature
        if not verify_signature(
            config=config, body=body, header_signature=x_webhook_signature
        ):
            log.warning("webhook_bad_signature", client_id=client_id)
            audit_step(
                lead_id="-",
                client_id=client_id,
                step="webhook_signature",
                status=AuditStatus.FAILED,
                error="invalid signature",
            )
            raise HTTPException(status_code=401, detail="invalid signature")

        # 3. Parse payload
        try:
            payload = await request.json()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"invalid json: {e}") from e

        # 4. Pick adapter
        try:
            adapter = get_form_adapter(plugin)
        except UnknownPluginError as e:
            log.warning("webhook_unknown_plugin", plugin=plugin, client_id=client_id)
            audit_step(
                lead_id="-",
                client_id=client_id,
                step="webhook_received",
                status=AuditStatus.FAILED,
                error=str(e),
                detail={"plugin": plugin},
            )
            raise HTTPException(status_code=400, detail=f"unknown plugin: {plugin}") from e

        # 5. Normalize
        try:
            lead: Lead = adapter.normalize(payload, client_id=client_id)
        except Exception as e:
            log.warning(
                "webhook_normalize_failed",
                plugin=plugin,
                client_id=client_id,
                error=str(e),
            )
            audit_step(
                lead_id="-",
                client_id=client_id,
                step="normalize",
                status=AuditStatus.FAILED,
                error=str(e),
                detail={"plugin": plugin, "payload_hash": hash_payload(payload)},
            )
            raise HTTPException(status_code=400, detail=f"normalize: {e}") from e

        # 6. Audit + enqueue (audit BEFORE the side effect)
        audit_step(
            lead_id=lead.lead_id,
            client_id=lead.client_id,
            step="webhook_received",
            status=AuditStatus.OK,
            payload=payload,
            detail={"plugin": plugin},
        )
        get_queue().enqueue(lead)

        return JSONResponse(
            status_code=202,
            content={"accepted": True, "lead_id": lead.lead_id},
        )

    # ---- Admin dashboard -------------------------------------------------

    @app.get("/admin", response_class=HTMLResponse)
    def admin_index(request: Request):
        # Show one row per client. We derive clients from the config dir
        # rather than from queue activity so empty clients appear too.
        base = settings.client_config_dir
        clients: list[dict] = []
        if base.exists():
            for path in sorted(base.glob("*.yaml")):
                if path.stem.startswith("_"):
                    continue
                try:
                    config = load_client_config(path.stem)
                except ClientConfigError:
                    continue
                leads = get_queue().list_leads_for_client(config.client_id, limit=10_000)
                clients.append(
                    {
                        "client_id": config.client_id,
                        "domain": config.domain,
                        "crm": config.crm,
                        "plugin": config.form_plugin,
                        "leads": len(leads),
                    }
                )
        return templates.TemplateResponse(
            request, "index.html", {"clients": clients, "queue": get_queue().queue_lengths()}
        )

    @app.get("/admin/{client_id}", response_class=HTMLResponse)
    def admin_client(
        request: Request,
        client_id: str = PathParam(..., pattern=r"^[A-Za-z0-9_\-]+$"),
    ):
        try:
            config = load_client_config(client_id)
        except ClientConfigError as e:
            raise HTTPException(status_code=404, detail="unknown client") from e
        leads = get_queue().list_leads_for_client(client_id, limit=500)
        return templates.TemplateResponse(
            request,
            "client.html",
            {"config": config, "leads": leads, "status_class": _status_class},
        )

    @app.get("/admin/{client_id}/leads/{lead_id}", response_class=HTMLResponse)
    def admin_lead(
        request: Request,
        client_id: str = PathParam(..., pattern=r"^[A-Za-z0-9_\-]+$"),
        lead_id: str = PathParam(..., pattern=r"^[A-Za-z0-9_\-]+$"),
    ):
        try:
            load_client_config(client_id)
        except ClientConfigError as e:
            raise HTTPException(status_code=404, detail="unknown client") from e
        lead = get_queue().get_lead(lead_id)
        if not lead or lead.client_id != client_id:
            raise HTTPException(status_code=404, detail="unknown lead")
        audit = get_audit_trail().read_for_lead(client_id, lead_id)
        return templates.TemplateResponse(
            request, "lead.html", {"lead": lead, "audit": audit, "status_class": _status_class}
        )

    return app


def _status_class(status: str) -> str:
    """Tiny helper for the templates — maps status → CSS class."""
    if status in (LeadStatus.CRM_DEAD_LETTER.value, LeadStatus.INVALID_EMAIL.value):
        return "bad"
    if status in (
        LeadStatus.AUTORESPONDED.value,
        LeadStatus.CRM_PUSHED.value,
        LeadStatus.REPLIED.value,
        LeadStatus.DONE.value,
    ):
        return "good"
    return "neutral"
