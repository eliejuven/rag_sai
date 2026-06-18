"""
Markdown -> PDF export for the 1-pager.

Custom fpdf2 renderer — replaces write_html() with element-by-element
rendering for precise control over typography, spacing, and layout.

Structure handled (in order, as produced by composer.py):
  - Company title line:  **Company | Period**
  - Header info block:   - **Label:** value  /  indented sub-bullets
  - Framing paragraph:   inline bold/regular mixed text
  - Section headings:    **N. Title:**
  - Body bullets:        - text(N)
  - Horizontal rule:     ---
  - Table heading:       **Actual … vs … (CS and CT):**
  - Sub-heading:         **Financial Information:**
  - Markdown table:      | col | col | …
  - Sign-off:            Best regards, / **Team [Área]**
  - Sources heading:     **Sources**
  - Footnotes:           (N) source text   — one per line
"""

import re
from fpdf import FPDF

# ── Unicode → Latin-1 normalization ─────────────────────────────────────────
_REPLACEMENTS = {
    "—": "-",   # em dash
    "–": "-",   # en dash
    "’": "'",   # right single quote
    "‘": "'",   # left single quote
    "“": '"',   # left double quote
    "”": '"',   # right double quote
    "…": "...", # ellipsis
    "•": "-",   # bullet
    "°": "o",   # degree sign
}


def _norm(text: str) -> str:
    for src, dst in _REPLACEMENTS.items():
        text = text.replace(src, dst)
    return text.encode("latin-1", errors="replace").decode("latin-1")


# ── Design tokens ─────────────────────────────────────────────────────────────
MX = 22        # horizontal margin, mm
MT = 18        # top margin, mm
MB = 14        # bottom margin, mm
F  = "helvetica"

# Font sizes (pt)
SZ_TITLE   = 12.0
SZ_SECTION = 9.5
SZ_BODY    = 8.5
SZ_TABLE   = 8.0
SZ_NOTE    = 7.0

# Line heights (mm) — proportional to font size
LH_TITLE   = 6.5
LH_SECTION = 5.5
LH_BODY    = 5.0
LH_TABLE   = 4.5
LH_NOTE    = 4.0

# Spacing before elements (mm)
SP_SECTION  = 7.0   # before a numbered section heading
SP_HEADING  = 3.0   # before a bold sub-heading (table title, etc.)
SP_PARA     = 3.0   # before a framing / plain paragraph
SP_BULLET   = 0.8   # between bullets (within a list)
SP_LIST_IN  = 2.0   # before first bullet in a list (after heading)
SP_TABLE    = 3.5   # before/after the financial table
SP_DIVIDER  = 4.0   # before/after ---
SP_FOOTNOTE = 0.8   # between footnote lines
SP_SOURCES  = 5.0   # before the Sources section

# Bullet geometry
B_INDENT    = 5.5   # mm: left edge of bullet text (from page margin)
B_SUB_IND  = 10.0  # mm: left edge of sub-bullet text

# Colors (R, G, B) — all grayscale-safe
C_TEXT     = (30,  30,  30 )
C_RULE     = (160, 160, 160)
C_SEC_RULE = (100, 100, 100)  # rule above section heading
C_SEC_BG   = (240, 240, 240)  # very light gray behind section heading
C_BULLET   = (140, 140, 140)
C_TBL_HEAD = (215, 215, 215)  # table header fill
C_TBL_ALT  = (248, 248, 248)  # alternating row fill
C_TBL_BD   = (195, 195, 195)  # table border
C_NOTE     = (130, 130, 130)
C_TITLE_RU = (80,  80,  80 )  # rule under title


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pw(pdf: FPDF) -> float:
    """Usable page width (mm)."""
    return pdf.w - 2 * MX


def _write_inline(pdf: FPDF, text: str, size: float, lh: float,
                  default_style: str = "") -> None:
    """
    Write text that may contain **bold** spans, switching fonts mid-line.
    Uses pdf.write() so text flows inline and wraps at the current left margin.
    """
    parts = re.split(r"(\*\*(?:[^*]|\*(?!\*))*\*\*)", text)
    for p in parts:
        if p.startswith("**") and p.endswith("**") and len(p) > 4:
            pdf.set_font(F, "B", size)
            pdf.write(lh, p[2:-2])
        else:
            pdf.set_font(F, default_style, size)
            if p:
                pdf.write(lh, p)


def _draw_rule(pdf: FPDF, color=C_RULE, thickness: float = 0.3) -> None:
    """Draw a full-width horizontal rule at the current y position."""
    pdf.set_draw_color(*color)
    pdf.set_line_width(thickness)
    y = pdf.get_y()
    pdf.line(MX, y, MX + _pw(pdf), y)
    pdf.set_line_width(0.2)


def _set_text(pdf: FPDF, color) -> None:
    pdf.set_text_color(*color)


# ── Markdown parser ──────────────────────────────────────────────────────────

def _classify(line: str, is_first_bold: bool) -> dict:
    """Return a dict with keys: type, text, (level for sub-bullets)."""
    stripped = line.rstrip()

    if not stripped:
        return {"type": "blank"}

    if stripped == "---":
        return {"type": "divider"}

    if stripped.startswith("|"):
        return {"type": "table_row", "text": stripped}

    # Footnote: (1) text  or  (12) text
    if re.match(r"^\(\d+\)\s", stripped):
        return {"type": "footnote", "text": stripped}

    # Sub-bullet (2 spaces + dash)
    if re.match(r"^  - ", stripped):
        return {"type": "sub_bullet", "text": stripped[4:]}

    # Top-level bullet
    if stripped.startswith("- "):
        return {"type": "bullet", "text": stripped[2:]}

    # Entirely bold line
    if stripped.startswith("**") and stripped.endswith("**") and stripped.count("**") >= 2:
        inner = stripped[2:-2].strip()
        if is_first_bold:
            return {"type": "title", "text": inner}
        # Section heading: starts with a digit and dot
        if re.match(r"^\d+\.", inner):
            return {"type": "section", "text": inner}
        # Sources / Fontes
        if inner.lower() in ("sources", "fontes"):
            return {"type": "sources_heading", "text": inner}
        # Other bold heading (table title, sub-heading)
        return {"type": "sub_heading", "text": inner}

    # Section heading that ends with colon and description after the bold close
    # e.g. **3. 2026 Outlook:** positive trend
    m = re.match(r"^\*\*(\d+\..+?)\*\*(.*)$", stripped)
    if m:
        return {"type": "section", "text": m.group(1).strip(), "suffix": m.group(2).strip()}

    # Plain paragraph (may contain inline **bold**)
    return {"type": "paragraph", "text": stripped}


def _parse(md: str) -> list[dict]:
    """Parse Markdown into a list of typed element dicts."""
    lines = _norm(md).splitlines()
    elements: list[dict] = []
    first_bold_seen = False

    for line in lines:
        el = _classify(line, is_first_bold=not first_bold_seen)
        if el["type"] == "title":
            first_bold_seen = True
        if el["type"] != "blank":
            elements.append(el)

    return elements


# ── Renderer ─────────────────────────────────────────────────────────────────

class _PdfRenderer:
    def __init__(self):
        self.pdf = FPDF(format="A4")
        self.pdf.set_auto_page_break(auto=True, margin=MB)
        self.pdf.set_margins(MX, MT, MX)
        self.pdf.add_page()
        _set_text(self.pdf, C_TEXT)

    def _pw(self) -> float:
        return _pw(self.pdf)

    # ── Element renderers ────────────────────────────────────────────────────

    def _title(self, text: str) -> None:
        pdf = self.pdf
        _set_text(pdf, C_TEXT)
        pdf.set_font(F, "B", SZ_TITLE)
        pdf.multi_cell(_pw(pdf), LH_TITLE, text, align="L")
        pdf.ln(1.5)
        _draw_rule(pdf, C_TITLE_RU, 0.5)
        pdf.ln(4.0)

    def _section(self, text: str, suffix: str = "") -> None:
        pdf = self.pdf
        pdf.ln(SP_SECTION)

        # Thin rule above
        _draw_rule(pdf, C_SEC_RULE, 0.4)
        pdf.ln(1.5)

        # Light gray background strip
        y = pdf.get_y()
        pdf.set_fill_color(*C_SEC_BG)
        pdf.rect(MX, y, _pw(pdf), LH_SECTION + 1.5, style="F")

        _set_text(pdf, C_TEXT)
        pdf.set_font(F, "B", SZ_SECTION)
        pdf.set_xy(MX + 1.5, y + 0.8)

        if suffix:
            # Section number/title is bold; suffix (e.g. "positive trend") is regular
            pdf.write(LH_SECTION, text + ": ")
            pdf.set_font(F, "", SZ_SECTION)
            pdf.write(LH_SECTION, suffix)
        else:
            pdf.write(LH_SECTION, text)

        pdf.ln(LH_SECTION + 2.0)

    def _sub_heading(self, text: str) -> None:
        pdf = self.pdf
        pdf.ln(SP_HEADING)
        _set_text(pdf, C_TEXT)
        pdf.set_font(F, "B", SZ_BODY)
        pdf.multi_cell(_pw(pdf), LH_BODY, text, align="L")
        pdf.ln(0.5)

    def _paragraph(self, text: str) -> None:
        pdf = self.pdf
        pdf.ln(SP_PARA)
        _set_text(pdf, C_TEXT)
        # Left margin stays at MX so write() wraps correctly
        pdf.set_x(MX)
        _write_inline(pdf, text, SZ_BODY, LH_BODY)
        pdf.ln(LH_BODY + 0.5)

    def _bullet(self, text: str, first_in_list: bool = False) -> None:
        pdf = self.pdf
        if first_in_list:
            pdf.ln(SP_LIST_IN)
        else:
            pdf.ln(SP_BULLET)

        _set_text(pdf, C_BULLET)
        pdf.set_font(F, "", SZ_BODY)
        pdf.set_x(MX)
        pdf.cell(B_INDENT, LH_BODY, "-")

        _set_text(pdf, C_TEXT)
        # Temporarily set left margin to indent so write() wraps correctly
        pdf.set_left_margin(MX + B_INDENT)
        pdf.set_x(MX + B_INDENT)
        _write_inline(pdf, text, SZ_BODY, LH_BODY)
        pdf.ln(LH_BODY)
        pdf.set_left_margin(MX)

    def _sub_bullet(self, text: str) -> None:
        pdf = self.pdf
        pdf.ln(SP_BULLET)

        _set_text(pdf, C_BULLET)
        pdf.set_font(F, "", SZ_BODY - 0.5)
        pdf.set_x(MX + B_INDENT)
        pdf.cell(B_SUB_IND - B_INDENT, LH_BODY, "-")

        _set_text(pdf, C_TEXT)
        pw_sub = _pw(pdf) - B_SUB_IND
        pdf.set_left_margin(MX + B_SUB_IND)
        pdf.set_x(MX + B_SUB_IND)
        _write_inline(pdf, text, SZ_BODY - 0.5, LH_BODY)
        pdf.ln(LH_BODY)
        pdf.set_left_margin(MX)

    def _divider(self) -> None:
        pdf = self.pdf
        pdf.ln(SP_DIVIDER)
        _draw_rule(pdf, C_RULE, 0.3)
        pdf.ln(SP_DIVIDER)

    def _table(self, rows: list[str]) -> None:
        """Render a Markdown table: first row = header, second = separator."""
        pdf = self.pdf
        pdf.ln(SP_TABLE)

        # Parse all rows
        parsed: list[list[str]] = []
        col_align: list[str] = []  # "L" or "R"

        for row in rows:
            cells = [c.strip() for c in row.strip("|").split("|")]
            # Detect separator row (---|---:)
            if all(re.match(r"^[-:]+$", c) for c in cells):
                col_align = ["R" if c.endswith(":") else "L" for c in cells]
                continue
            parsed.append(cells)

        if not parsed:
            return

        n_cols = max(len(r) for r in parsed)
        if not col_align:
            col_align = ["L"] + ["R"] * (n_cols - 1)

        # Column widths: first col gets 40%, rest split equally
        pw = _pw(pdf)
        if n_cols == 1:
            widths = [pw]
        else:
            w0 = pw * 0.40
            w_rest = (pw - w0) / (n_cols - 1)
            widths = [w0] + [w_rest] * (n_cols - 1)

        row_h = LH_TABLE + 2.0

        for i, row in enumerate(parsed):
            is_header = (i == 0)
            is_alt    = (i > 0 and i % 2 == 0)

            if is_header:
                pdf.set_fill_color(*C_TBL_HEAD)
            elif is_alt:
                pdf.set_fill_color(*C_TBL_ALT)
            else:
                pdf.set_fill_color(255, 255, 255)

            pdf.set_draw_color(*C_TBL_BD)
            pdf.set_line_width(0.2)
            _set_text(pdf, C_TEXT)

            for j, cell_text in enumerate(row):
                w = widths[j] if j < len(widths) else widths[-1]
                align = col_align[j] if j < len(col_align) else "L"
                style = "B" if is_header else ""
                pdf.set_font(F, style, SZ_TABLE)

                # Pad cell content
                padded = f"  {cell_text}  " if align == "L" else f"{cell_text}  "
                pdf.cell(w, row_h, padded, border=1, align=align, fill=True)

            pdf.ln(row_h)

        pdf.ln(SP_TABLE)

    def _sources_heading(self, text: str) -> None:
        pdf = self.pdf
        pdf.ln(SP_SOURCES)
        _draw_rule(pdf, C_RULE, 0.3)
        pdf.ln(3.0)
        _set_text(pdf, C_TEXT)
        pdf.set_font(F, "B", SZ_BODY)
        pdf.multi_cell(_pw(pdf), LH_BODY, text, align="L")
        pdf.ln(2.0)

    def _footnote(self, text: str, first: bool = False) -> None:
        pdf = self.pdf
        if not first:
            pdf.ln(SP_FOOTNOTE)
        _set_text(pdf, C_NOTE)
        pdf.set_left_margin(MX + 4.0)
        pdf.set_x(MX + 4.0)
        _write_inline(pdf, text, SZ_NOTE, LH_NOTE, default_style="")
        pdf.ln(LH_NOTE)
        pdf.set_left_margin(MX)

    # ── Main render loop ─────────────────────────────────────────────────────

    def render(self, elements: list[dict]) -> bytes:
        prev_type = None
        in_bullet_list = False
        in_footnotes   = False
        first_footnote = True
        table_buffer: list[str] = []

        def flush_table():
            nonlocal table_buffer
            if table_buffer:
                self._table(table_buffer)
                table_buffer = []

        for el in elements:
            t = el["type"]

            # Flush accumulated table rows when a non-table element arrives
            if t != "table_row" and table_buffer:
                flush_table()

            if t == "title":
                self._title(el["text"])
                in_bullet_list = False

            elif t == "section":
                flush_table()
                self._section(el["text"], el.get("suffix", ""))
                in_bullet_list = False

            elif t == "sub_heading":
                flush_table()
                self._sub_heading(el["text"])
                in_bullet_list = False

            elif t == "paragraph":
                flush_table()
                self._paragraph(el["text"])
                in_bullet_list = False

            elif t == "bullet":
                first_in = not in_bullet_list or prev_type not in ("bullet", "sub_bullet")
                self._bullet(el["text"], first_in_list=first_in)
                in_bullet_list = True

            elif t == "sub_bullet":
                self._sub_bullet(el["text"])
                in_bullet_list = True

            elif t == "divider":
                flush_table()
                self._divider()
                in_bullet_list = False

            elif t == "table_row":
                table_buffer.append(el["text"])

            elif t == "sources_heading":
                flush_table()
                self._sources_heading(el["text"])
                in_bullet_list = False
                in_footnotes   = True
                first_footnote = True

            elif t == "footnote":
                self._footnote(el["text"], first=first_footnote)
                first_footnote = False
                in_bullet_list = False

            prev_type = t

        flush_table()

        _set_text(self.pdf, C_TEXT)
        return bytes(self.pdf.output())


# ── Public API ───────────────────────────────────────────────────────────────

def markdown_to_pdf(md_text: str) -> bytes:
    """Render a credit committee Markdown 1-pager to PDF bytes."""
    elements = _parse(md_text)
    return _PdfRenderer().render(elements)
