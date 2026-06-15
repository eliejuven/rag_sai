"""
Markdown -> PDF export for the 1-pager (and memo, if needed later).

fpdf2's core fonts only support Latin-1, so "smart" punctuation that LLMs
commonly produce (em/en-dashes, curly quotes, ellipsis) is normalized to
Latin-1-safe equivalents before rendering. Portuguese accented characters
(á, ã, ç, ...) are already within Latin-1 and render fine.
"""

import markdown
from fpdf import FPDF

_UNICODE_REPLACEMENTS = {
    "—": "-",   # em dash
    "–": "-",   # en dash
    "‘": "'",   # left single quote
    "’": "'",   # right single quote
    "“": '"',   # left double quote
    "”": '"',   # right double quote
    "…": "...",  # ellipsis
    "•": "-",   # bullet
}


def _normalize_text(text: str) -> str:
    for src, dst in _UNICODE_REPLACEMENTS.items():
        text = text.replace(src, dst)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def markdown_to_pdf(md_text: str) -> bytes:
    """Render a Markdown string to a PDF document, returned as bytes."""
    html = markdown.markdown(_normalize_text(md_text), extensions=["tables"])

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("helvetica", size=10)
    pdf.write_html(html)
    return bytes(pdf.output())
