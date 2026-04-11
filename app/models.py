from pydantic import BaseModel


class IngestResponse(BaseModel):
    message: str
    document_ids: list[str]
    total_pages: int
    total_chunks: int


class QueryRequest(BaseModel):
    question: str


class ChunkResult(BaseModel):
    text: str
    filename: str
    page_number: int
    score: float


class QueryResponse(BaseModel):
    answer: str
    chunks: list[ChunkResult]
