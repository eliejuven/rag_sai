import fitz


def _table_to_markdown(table) -> str:
    rows = table.extract()
    if not rows:
        return ""

    # Normalize: replace None with empty string
    rows = [[(cell or "") for cell in row] for row in rows]

    col_widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]

    def fmt_row(row):
        return "| " + " | ".join(str(cell).ljust(col_widths[i]) for i, cell in enumerate(row)) + " |"

    lines = [fmt_row(rows[0])]
    lines.append("| " + " | ".join("-" * w for w in col_widths) + " |")
    for row in rows[1:]:
        lines.append(fmt_row(row))

    return "\n".join(lines)


def extract_text_from_pdf(pdf_bytes: bytes) -> list[dict]:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        parts = []

        # Extract tables first and track their bounding boxes
        tables = page.find_tables()
        table_bboxes = []
        for table in tables:
            md = _table_to_markdown(table)
            if md:
                parts.append(("table", table.bbox, md))
                table_bboxes.append(fitz.Rect(table.bbox))

        # Extract text blocks, skipping areas covered by tables
        blocks = page.get_text("blocks")
        for block in blocks:
            x0, y0, x1, y1, text, *_ = block
            block_rect = fitz.Rect(x0, y0, x1, y1)
            if any(block_rect.intersects(tb) for tb in table_bboxes):
                continue
            text = text.strip()
            if text:
                parts.append(("text", y0, text))

        # Sort all parts top-to-bottom by vertical position
        parts.sort(key=lambda p: p[1] if isinstance(p[1], float) else p[1][1])

        page_text = "\n\n".join(p[2] for p in parts)
        if page_text.strip():
            pages.append({"page_number": page_num + 1, "text": page_text})

    doc.close()
    return pages
