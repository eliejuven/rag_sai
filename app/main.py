from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

app = FastAPI(
    title="RAG Pipeline",
    description="Retrieval-Augmented Generation pipeline for PDF knowledge bases",
    version="0.1.0",
)


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
