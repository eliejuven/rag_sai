"""
Credit Committee 1-Pager — Router (Pipeline B).

Endpoints:
  POST  /committee/generate/stream    SSE stream — main entry for /1-pager UI
  POST  /committee/generate           Synchronous version
  GET   /committee/{cnpj}             Latest CommitteeReport (JSON)
  GET   /committee/{cnpj}/report.pdf  PDF version (?lang=pt|en)
  GET   /committee/{cnpj}/bank-context
  PUT   /committee/{cnpj}/bank-context
"""

import asyncio
import json
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from app.analysis.committee.pipeline import (
    generate_committee_report,
    load_bank_context,
    load_committee_report,
    save_bank_context,
)
from app.analysis.committee.schemas import BankContext, CommitteeReport
from app.analysis.pdf_export import markdown_to_pdf

router = APIRouter()


def _cnpj_digits(cnpj: str) -> str:
    return re.sub(r"\D", "", cnpj)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CommitteeRequest(BaseModel):
    company: str
    bank_context: Optional[BankContext] = None  # optional inline overrides


# ---------------------------------------------------------------------------
# POST /committee/generate/stream   (SSE)
# ---------------------------------------------------------------------------


@router.post("/generate/stream")
async def generate_committee_stream(request: CommitteeRequest):
    """
    SSE endpoint.  Streams progress messages then the final CommitteeReport.

    Event format:
      data: {"type": "progress", "message": "..."}
      data: {"type": "result",   "report": {...}}
      data: {"type": "error",    "message": "..."}
    """
    company = request.company.strip()
    if not company:
        raise HTTPException(status_code=400, detail="Company cannot be empty.")

    queue: asyncio.Queue = asyncio.Queue()

    def _sse(payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    async def emit(event_type: str, message: str) -> None:
        if event_type == "progress":
            await queue.put({"type": "progress", "message": message})
        elif event_type == "result":
            # message is already a JSON string from model_dump_json()
            report_dict = json.loads(message)
            await queue.put({"type": "result", "report": report_dict})

    async def run_pipeline() -> None:
        try:
            await generate_committee_report(
                company_name=company,
                emit=emit,
                bank_context_override=request.bank_context,
            )
        except Exception as exc:
            await queue.put({"type": "error", "message": str(exc)})
        finally:
            await queue.put(None)  # sentinel

    async def event_generator():
        asyncio.create_task(run_pipeline())
        while True:
            item = await queue.get()
            if item is None:
                break
            yield _sse(item)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# POST /committee/generate   (synchronous)
# ---------------------------------------------------------------------------


@router.post("/generate", response_model=CommitteeReport)
async def generate_committee(request: CommitteeRequest):
    company = request.company.strip()
    if not company:
        raise HTTPException(status_code=400, detail="Company cannot be empty.")

    try:
        return await generate_committee_report(
            company_name=company,
            bank_context_override=request.bank_context,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ---------------------------------------------------------------------------
# GET /committee/{cnpj}
# ---------------------------------------------------------------------------


@router.get("/{cnpj}", response_model=CommitteeReport)
async def get_committee_report(cnpj: str):
    """Return the latest persisted CommitteeReport for a CNPJ."""
    report = load_committee_report(cnpj)
    if report is None:
        raise HTTPException(status_code=404, detail=f"No committee report found for CNPJ {cnpj}.")
    return report


# ---------------------------------------------------------------------------
# GET /committee/{cnpj}/report.pdf
# ---------------------------------------------------------------------------


@router.get("/{cnpj}/report.pdf")
async def get_committee_pdf(cnpj: str, lang: str = Query("pt", pattern="^(pt|en)$")):
    """Render the latest committee report as a PDF (?lang=pt or ?lang=en)."""
    report = load_committee_report(cnpj)
    if report is None:
        raise HTTPException(status_code=404, detail=f"No committee report found for CNPJ {cnpj}.")

    md = report.report_en_md if lang == "en" else report.report_pt_md
    pdf_bytes = markdown_to_pdf(md)
    digits = _cnpj_digits(cnpj)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="committee-{digits}-{lang}.pdf"'},
    )


# ---------------------------------------------------------------------------
# GET /committee/{cnpj}/bank-context
# ---------------------------------------------------------------------------


@router.get("/{cnpj}/bank-context", response_model=BankContext)
async def get_bank_context(cnpj: str):
    """Return the stored BankContext for a CNPJ (all-None defaults if not yet set)."""
    return load_bank_context(cnpj)


# ---------------------------------------------------------------------------
# PUT /committee/{cnpj}/bank-context
# ---------------------------------------------------------------------------


@router.put("/{cnpj}/bank-context", response_model=BankContext)
async def update_bank_context(cnpj: str, ctx: BankContext):
    """
    Merge provided BankContext fields into the stored context for a CNPJ.
    Fields set to null in the request body are ignored (existing values kept).
    Returns the merged result.
    """
    existing = load_bank_context(cnpj)
    merged = existing.model_copy(update=ctx.model_dump(exclude_none=True))
    from datetime import datetime
    merged = merged.model_copy(update={"updated_at": datetime.now()})
    save_bank_context(cnpj, merged)
    return merged
