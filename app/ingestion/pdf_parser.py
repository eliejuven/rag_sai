import io
import pdfplumber


def _words_to_table(words: list[dict], y_tolerance: float = 3.0) -> str:
    """Reconstruct a table from word positions by grouping into rows and columns."""
    if not words:
        return ""

    # Group words into rows by vertical position
    rows: list[list[dict]] = []
    current_row: list[dict] = []
    last_top = None

    for word in sorted(words, key=lambda w: (round(w["top"] / y_tolerance), w["x0"])):
        if last_top is None or abs(word["top"] - last_top) <= y_tolerance:
            current_row.append(word)
        else:
            if current_row:
                rows.append(current_row)
            current_row = [word]
        last_top = word["top"]

    if current_row:
        rows.append(current_row)

    if not rows:
        return ""

    # Build plain text rows — each row is words joined by spaces
    lines = [" ".join(w["text"] for w in row) for row in rows]
    return "\n".join(lines)


def extract_text_from_pdf(pdf_bytes: bytes) -> list[dict]:
    pages = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_num, page in enumerate(pdf.pages):
            # Use word-level extraction to preserve row structure
            words = page.extract_words(
                x_tolerance=5,
                y_tolerance=3,
                keep_blank_chars=False,
            )

            if not words:
                continue

            text = _words_to_table(words)
            if text.strip():
                pages.append({"page_number": page_num + 1, "text": text})

    return pages
