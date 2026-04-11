"""In-memory storage for parsed documents.

Structure:
    documents = {
        "<document_id>": {
            "filename": "report.pdf",
            "pages": [
                {"page_number": 1, "text": "..."},
                {"page_number": 2, "text": "..."},
            ]
        }
    }
"""

documents: dict[str, dict] = {}
