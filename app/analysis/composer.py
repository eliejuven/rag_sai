"""
Phase 5 — Composer.

Renders the SectionOutputs produced by Phase 4's agents into Markdown:
- compose_memo(): full memo — every statement, grouped by fact/inference/
  judgment within each section.
- compose_one_pager(): condensed view — one highlight per section (its
  judgment, or its lead fact if the section has no judgment).

Both composers read from the same `sections` list and never regenerate or
recompute anything — the one-pager is strictly a selection of statements
already present in the memo, so the two can never disagree.

Citation rendering reuses the existing numbered "[n]" convention
(app/generation/prompts.py): each composed document gets its own
deduplicated reference list, with statements citing into it by index.

Output is Markdown only — PDF/Word export is a later concern (see TODO.md
Phase 5).
"""

from app.analysis.schemas import Citation, CompanyDossier, SectionOutput, TaggedStatement

_TYPE_HEADINGS = {
    "fact": "Fatos",
    "inference": "Análise",
    "judgment": "Avaliação",
}


def _citation_key(c: Citation) -> tuple:
    return (c.document_id, c.section, c.page_number)


class _CitationIndex:
    """Dedupes Citations within one composed document, assigning "[n]" ids."""

    def __init__(self):
        self._citations: list[Citation] = []
        self._by_key: dict[tuple, int] = {}

    def id_for(self, citation: Citation) -> int:
        key = _citation_key(citation)
        existing = self._by_key.get(key)
        if existing is not None:
            return existing
        self._citations.append(citation)
        new_id = len(self._citations)
        self._by_key[key] = new_id
        return new_id

    def markers_for(self, statement: TaggedStatement) -> str:
        if not statement.citations:
            return ""
        ids = sorted({self.id_for(c) for c in statement.citations})
        return "".join(f"[{i}]" for i in ids)

    def render_references(self) -> str:
        if not self._citations:
            return "(nenhuma referência)"
        lines = []
        for i, c in enumerate(self._citations, 1):
            label = c.section_label or c.section or "—"
            page = f", p. {c.page_number}" if c.page_number else ""
            lines.append(f"[{i}] {c.filename} — {label}{page}")
        return "\n".join(lines)


def _render_statement_line(statement: TaggedStatement, index: _CitationIndex) -> str:
    suffix = ""
    if statement.type == "inference" and statement.derived_from:
        suffix = f" _(estimativa: {'; '.join(statement.derived_from)})_"
    elif statement.type == "judgment" and statement.basis:
        suffix = f" _(base: {statement.basis})_"
    markers = index.markers_for(statement)
    if markers:
        markers = f" {markers}"
    return f"- {statement.text}{suffix}{markers}"


def _render_section_full(section: SectionOutput, index: _CitationIndex) -> str:
    lines = [f"## {section.title}"]
    if not section.statements:
        lines.append("\n_(nenhuma informação disponível)_")
        return "\n".join(lines)

    by_type: dict[str, list[TaggedStatement]] = {"fact": [], "inference": [], "judgment": []}
    for s in section.statements:
        by_type[s.type].append(s)

    for stype, heading in _TYPE_HEADINGS.items():
        statements = by_type[stype]
        if not statements:
            continue
        lines.append(f"\n**{heading}**\n")
        lines.extend(_render_statement_line(s, index) for s in statements)

    return "\n".join(lines)


def _header(dossier: CompanyDossier) -> str:
    return (
        f"# {dossier.name} ({dossier.trade_name})\n\n"
        f"CNPJ: {dossier.cnpj} | Setor: {dossier.sector or 'n/d'} | "
        f"Gerado em: {dossier.generated_at.date().isoformat()}"
    )


def _references_block(index: _CitationIndex) -> str:
    return f"---\n\n## Referências\n\n{index.render_references()}"


def compose_memo(dossier: CompanyDossier, sections: list[SectionOutput]) -> str:
    """Render the full memo: every statement, grouped by fact/inference/judgment per section."""
    index = _CitationIndex()
    body = "\n\n".join(_render_section_full(s, index) for s in sections)
    return f"{_header(dossier)}\n\n{body}\n\n{_references_block(index)}"


def _section_highlight(section: SectionOutput) -> TaggedStatement | None:
    """Pick the one statement that best represents this section in the 1-pager:
    its judgment (the analytical takeaway), or its lead fact/inference if it has none."""
    for statement in section.statements:
        if statement.type == "judgment":
            return statement
    return section.statements[0] if section.statements else None


def compose_one_pager(dossier: CompanyDossier, sections: list[SectionOutput]) -> str:
    """Render a condensed view: one highlight statement per section."""
    index = _CitationIndex()
    lines = [_header(dossier), ""]

    for section in sections:
        highlight = _section_highlight(section)
        if highlight is None:
            continue
        markers = index.markers_for(highlight)
        if markers:
            markers = f" {markers}"
        lines.append(f"**{section.title}**: {highlight.text}{markers}")

    body = "\n\n".join(lines)
    return f"{body}\n\n{_references_block(index)}"
