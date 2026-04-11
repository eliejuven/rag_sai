from pydantic import BaseModel


class IngestResponse(BaseModel):
    message: str
    document_ids: list[str]
    total_pages: int
    total_chunks: int
