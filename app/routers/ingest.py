import uuid

from fastapi import APIRouter, UploadFile, HTTPException

from app.ingestion.pdf_parser import extract_text_from_pdf
from app.ingestion.chunker import chunk_pages
from app.models import IngestResponse
from app import storage

router = APIRouter()


@router.post("/ingest", response_model=IngestResponse)
async def ingest_pdfs(files: list[UploadFile]):
    """Upload one or more PDF files for ingestion into the knowledge base."""
    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")

    document_ids = []
    total_pages = 0
    total_chunks = 0

    for file in files:
        if file.content_type != "application/pdf":
            raise HTTPException(
                status_code=400,
                detail=f"File '{file.filename}' is not a PDF.",
            )

        pdf_bytes = await file.read()
        pages = extract_text_from_pdf(pdf_bytes)

        if not pages:
            raise HTTPException(
                status_code=400,
                detail=f"No text could be extracted from '{file.filename}'.",
            )

        doc_id = str(uuid.uuid4())
        storage.documents[doc_id] = {
            "filename": file.filename,
            "pages": pages,
        }

        doc_chunks = chunk_pages(pages)
        for chunk in doc_chunks:
            chunk["document_id"] = doc_id
            chunk["filename"] = file.filename

        storage.chunks.extend(doc_chunks)

        document_ids.append(doc_id)
        total_pages += len(pages)
        total_chunks += len(doc_chunks)

    return IngestResponse(
        message=f"Successfully ingested {len(files)} file(s).",
        document_ids=document_ids,
        total_pages=total_pages,
        total_chunks=total_chunks,
    )
