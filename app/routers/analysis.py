import asyncio
import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.analysis.pipeline import generate_credit_analysis
from app.analysis.schemas import AnalysisRun
from app.models import AnalysisRequest

router = APIRouter()

ANALYSIS_RUNS_DIR = Path(__file__).parent.parent.parent / "data" / "analysis_runs"


def _cnpj_digits(cnpj: str) -> str:
    return re.sub(r"\D", "", cnpj)


@router.post("/analysis/generate", response_model=AnalysisRun)
async def generate_analysis(request: AnalysisRequest):
    """Run the full credit-analysis pipeline and return the resulting AnalysisRun."""
    company = request.company.strip()
    if not company:
        raise HTTPException(status_code=400, detail="Company cannot be empty.")

    try:
        return await generate_credit_analysis(company, year=request.year)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/analysis/generate/stream")
async def generate_analysis_stream(request: AnalysisRequest):
    """
    SSE endpoint. Streams progress messages then the final AnalysisRun.

    Event format:
      data: {"type": "progress", "message": "..."}
      data: {"type": "result",   "run": {...}}
      data: {"type": "error",    "message": "..."}
    """
    company = request.company.strip()
    if not company:
        raise HTTPException(status_code=400, detail="Company cannot be empty.")

    queue: asyncio.Queue = asyncio.Queue()

    def _sse(payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    async def emit(message: str) -> None:
        await queue.put({"type": "progress", "message": message})

    async def run_pipeline() -> None:
        try:
            run = await generate_credit_analysis(company, year=request.year, progress=emit)
            await queue.put({"type": "result", "run": run.model_dump(mode="json")})
        except Exception as e:
            await queue.put({"type": "error", "message": str(e)})
        finally:
            await queue.put(None)  # sentinel — stream is done

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
            "X-Accel-Buffering": "no",  # disables nginx buffering
        },
    )


@router.get("/analysis/{cnpj}", response_model=AnalysisRun)
async def get_latest_analysis(cnpj: str):
    """Return the most recently persisted AnalysisRun for a CNPJ, if any."""
    cnpj_dir = ANALYSIS_RUNS_DIR / _cnpj_digits(cnpj)
    if not cnpj_dir.exists():
        raise HTTPException(status_code=404, detail=f"No analysis found for CNPJ {cnpj}.")

    run_dirs = sorted((p for p in cnpj_dir.iterdir() if p.is_dir()), reverse=True)
    if not run_dirs:
        raise HTTPException(status_code=404, detail=f"No analysis found for CNPJ {cnpj}.")

    run_path = run_dirs[0] / "run.json"
    if not run_path.exists():
        raise HTTPException(status_code=404, detail=f"No analysis found for CNPJ {cnpj}.")

    return AnalysisRun.model_validate_json(run_path.read_text(encoding="utf-8"))
