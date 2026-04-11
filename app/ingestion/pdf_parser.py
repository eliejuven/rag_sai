import fitz


def extract_text_from_pdf(pdf_bytes: bytes) -> list[dict]:
    """Extract text content from a PDF file, page by page.

    Returns a list of dicts with keys: page_number (1-indexed), text.
    Skips pages that contain no meaningful text.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []

    for page_num in range(len(doc)):
        text = doc[page_num].get_text().strip()
        if text:
            pages.append({"page_number": page_num + 1, "text": text})

    doc.close()
    return pages
