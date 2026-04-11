"""In-memory storage for parsed documents and their chunks.

documents: raw parsed pages, keyed by document_id.
chunks: list of all chunks across all documents, used for search.
"""

documents: dict[str, dict] = {}

chunks: list[dict] = []
