"""
Credit Committee 1-Pager — Template Composer.

_build_financial_table()      Phase 3 — pure Python, no LLM
compose_committee_template_pt() Phase 5 — Markdown template in Portuguese
translate_to_en()              Phase 6 — single LLM translation call
"""

import re
from typing import Optional

from app.analysis.committee.schemas import (
    BankContext,
    CommitteeHeaderOutput,
    CommitteeSection,
    CommitteeReport,
    FinancialTableRow,
)
from app.analysis.schemas import CompanyDossier, DisclosedMetric, FinancialLineItem
from app.generation.llm import chat_completion

_FILL = "[PREENCHER]"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt_mm(value: float, scale: str) -> str:
    """Convert a financial line-item value to R$ MM string."""
    scale = scale.upper()
    if scale in ("MIL", "THOUSANDS", "R$ MIL", "R$ MILHARES"):
        mm = value / 1_000
    elif scale in ("UNIDADE", "UNIT", "UNITS", "R$"):
        mm = value / 1_000_000
    else:
        # assume already MM
        mm = value
    return f"{mm:,.1f}"


def _fmt_pct(value: float) -> str:
    return f"{value:.1f}%"


def _fmt_x(value: float) -> str:
    return f"{value:.2f}x"


def _period_sort_key(label: str) -> tuple:
    m = re.match(r"FY (\d{4})", label)
    if m:
        return (int(m.group(1)), 99)
    m = re.match(r"(\d+)M (\d{4})", label)
    if m:
        return (int(m.group(2)), int(m.group(1)))
    return (0, 0)


def _latest_line_item(
    items: list[FinancialLineItem],
    *,
    account_code: str,
    statement_type: str,
) -> Optional[FinancialLineItem]:
    candidates = [
        li for li in items
        if li.account_code == account_code and li.statement_type == statement_type
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda li: _period_sort_key(li.period_label))


def _latest_metric(
    metrics: list[DisclosedMetric],
    *label_keywords: str,
    exclude_keywords: list[str] | None = None,
) -> Optional[DisclosedMetric]:
    """Find the most-recent DisclosedMetric whose label contains ALL label_keywords."""
    exclude = [k.lower() for k in (exclude_keywords or [])]
    candidates = [
        m for m in metrics
        if all(kw.lower() in m.label.lower() for kw in label_keywords)
        and not any(ex in m.label.lower() for ex in exclude)
        and m.value is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda m: _period_sort_key(m.period_label))


def _proj_val(bank_context: BankContext, key: str, proj: str) -> str:
    """Extract a projected value from BankContext.projetado_cs/ct."""
    d = bank_context.projetado_cs if proj == "cs" else bank_context.projetado_ct
    if d is None:
        return _FILL
    v = d.get(key)
    if v is None:
        return _FILL
    return str(v)


# ---------------------------------------------------------------------------
# Phase 3 — Financial table builder
# ---------------------------------------------------------------------------


def _build_financial_table(
    dossier: CompanyDossier, bank_context: BankContext
) -> list[FinancialTableRow]:
    items = dossier.financial_line_items
    metrics = dossier.disclosed_metrics

    # --- Faturamento: DRE account 3.01 ---
    fat_item = _latest_line_item(items, account_code="3.01", statement_type="DRE_con")
    if fat_item:
        fat_val = _fmt_mm(fat_item.value, fat_item.scale)
        fat_num: float | None = fat_item.value / 1_000 if fat_item.scale.upper() in (
            "MIL", "THOUSANDS", "R$ MIL", "R$ MILHARES"
        ) else fat_item.value
    else:
        fat_val = "—"
        fat_num = None

    # --- EBITDA: disclosed metric ---
    ebitda_m = _latest_metric(metrics, "EBITDA") or _latest_metric(metrics, "LAJIDA")
    if ebitda_m and ebitda_m.value is not None:
        ebitda_num = ebitda_m.value
        # if unit says % or the value looks like a ratio, skip
        if ebitda_m.unit and "%" in ebitda_m.unit:
            ebitda_val = "—"
            ebitda_num = None
        else:
            # convert to MM if unit says milhões/MM already, else treat as is
            if ebitda_m.unit and ("milhão" in ebitda_m.unit.lower() or "milhões" in ebitda_m.unit.lower() or "mm" in ebitda_m.unit.lower()):
                ebitda_val = f"{ebitda_num:,.1f}"
            else:
                ebitda_val = f"{ebitda_num:,.1f}"
    else:
        ebitda_val = "—"
        ebitda_num = None

    # --- Margem EBITDA ---
    margem_m = (
        _latest_metric(metrics, "Margem", "EBITDA")
        or _latest_metric(metrics, "Margem", "LAJIDA")
    )
    if margem_m and margem_m.value is not None:
        margem_val = _fmt_pct(margem_m.value)
    elif ebitda_num is not None and fat_num and fat_num != 0:
        margem_val = _fmt_pct((ebitda_num / fat_num) * 100)
    else:
        margem_val = "—"

    # --- Dívida Líquida ---
    dl_m = (
        _latest_metric(metrics, "Dívida Líquida")
        or _latest_metric(metrics, "Divida Liquida")
        or _latest_metric(metrics, "Net Debt")
    )
    dl_val = f"{dl_m.value:,.1f}" if dl_m and dl_m.value is not None else "—"

    # --- Alavancagem ---
    alav_m = (
        _latest_metric(metrics, "Alavancagem")
        or _latest_metric(metrics, "Leverage")
        or _latest_metric(metrics, "DL/EBITDA")
    )
    alav_val = _fmt_x(alav_m.value) if alav_m and alav_m.value is not None else "—"

    def _cs(key: str) -> str:
        return _proj_val(bank_context, key, "cs")

    def _ct(key: str) -> str:
        return _proj_val(bank_context, key, "ct")

    return [
        FinancialTableRow(
            indicator="Faturamento (R$ MM)",
            indicator_en="Revenue (R$ MM)",
            realizado=fat_val,
            projetado_cs=_cs("faturamento"),
            projetado_ct=_ct("faturamento"),
        ),
        FinancialTableRow(
            indicator="EBITDA (R$ MM)",
            indicator_en="EBITDA (R$ MM)",
            realizado=ebitda_val,
            projetado_cs=_cs("ebitda"),
            projetado_ct=_ct("ebitda"),
        ),
        FinancialTableRow(
            indicator="Margem EBITDA (%)",
            indicator_en="EBITDA Margin (%)",
            realizado=margem_val,
            projetado_cs=_cs("margem_ebitda"),
            projetado_ct=_ct("margem_ebitda"),
        ),
        FinancialTableRow(
            indicator="Dívida Líquida (R$ MM)",
            indicator_en="Net Debt (R$ MM)",
            realizado=dl_val,
            projetado_cs=_cs("divida_liquida"),
            projetado_ct=_ct("divida_liquida"),
        ),
        FinancialTableRow(
            indicator="Alavancagem (x)",
            indicator_en="Leverage (x)",
            realizado=alav_val,
            projetado_cs=_cs("alavancagem"),
            projetado_ct=_ct("alavancagem"),
        ),
    ]


# ---------------------------------------------------------------------------
# Phase 5 — Portuguese template composer
# ---------------------------------------------------------------------------


def _opt(value: str | float | None, fmt: str = "") -> str:
    """Return formatted value or [PREENCHER] if None."""
    if value is None:
        return _FILL
    if fmt == "pct":
        return f"{value:.1f}%"
    if fmt == "mm":
        return f"{value:,.1f}"
    return str(value)


def compose_committee_template_pt(
    dossier: CompanyDossier,
    header_output: CommitteeHeaderOutput,
    consolidado: CommitteeSection,
    holding: CommitteeSection,
    perspectivas: CommitteeSection,
    financial_table: list[FinancialTableRow],
    bank_context: BankContext,
) -> str:
    trade_name = dossier.trade_name or dossier.name
    period = consolidado.year_label or perspectivas.year_label or "N/A"

    # Derive next year for Perspectivas header
    try:
        next_year = str(int(re.search(r"\d{4}", period).group()) + 1)
    except (AttributeError, ValueError):
        next_year = "próximo ano"

    # --- Header block ---
    limite = _opt(bank_context.limite_mm, "mm")
    risco = _opt(bank_context.risco_mm, "mm")
    run_off_pct = _opt(bank_context.run_off_pct, "pct")
    run_off_assets_str = ", ".join(bank_context.run_off_assets) if bank_context.run_off_assets else _FILL
    run_off_holding_pct = _opt(bank_context.run_off_holding_pct, "pct")
    share_name = _opt(bank_context.share_banco_name)
    share_pct = _opt(bank_context.share_banco_pct, "pct")

    # Ratings: use extracted first, then bank_context, then PREENCHER
    er = header_output.extracted_ratings
    rating_cs = bank_context.rating_cs or er.get("cs") or _FILL
    rating_holding = bank_context.rating_holding or er.get("holding") or _FILL
    rating_maduros = bank_context.rating_ativos_maduros or er.get("ativos_maduros") or _FILL

    ultimo_comite = _opt(bank_context.ultimo_comite)

    grau = header_output.grau_preocupacao
    grau_reasoning = header_output.grau_preocupacao_reasoning
    proximos_passos = header_output.proximos_passos

    # --- Narrative bullets ---
    def _bullets(section: CommitteeSection) -> str:
        return "\n".join(f"- {b}" for b in section.bullets)

    consolidado_bullets = _bullets(consolidado)
    holding_bullets = _bullets(holding)
    perspectivas_bullets = _bullets(perspectivas)

    # Single "N/A — holding" bullet check
    holding_header_note = ""
    if len(holding.bullets) == 1 and "single-entity" in holding.bullets[0].lower():
        holding_header_note = " *(estrutura holding não aplicável)*"

    # --- Financial table ---
    table_year = period
    banco_name = bank_context.share_banco_name or "Banco"

    def _trow(row: FinancialTableRow) -> str:
        return f"| {row.indicator} | {row.realizado} | {row.projetado_cs} | {row.projetado_ct} |"

    table_rows = "\n".join(_trow(r) for r in financial_table)

    return f"""\
**{trade_name} | Resultados {period}**

- **Limite:** R$ {limite} MM
- **Risco:** R$ {risco} MM (disponibilidade apenas em cartão de crédito)
  - 100% Run-off: {run_off_pct} do risco em ativos maduros ({run_off_assets_str}); {run_off_holding_pct} na holding
  - Share {share_name}: {share_pct}
- **Ratings:** {rating_cs} CS, {rating_holding} Holding, ativos maduros > {rating_maduros}
- **Último comitê:** {ultimo_comite}
- **Grau de preocupação:** {grau} ({grau_reasoning})
- **Próximos passos:** {proximos_passos}

{header_output.framing_paragraph}

**1. Highlights {period} (Consolidado):**

{consolidado_bullets}

**2. Highlights {period} (Holding):**{holding_header_note}

{holding_bullets}

**3. Perspectivas {next_year}:** tendência positiva

{perspectivas_bullets}

---

**Realizado {table_year} vs projetado {banco_name} (CS e CT):**

**Informações financeiras:**

| Indicador | Realizado {table_year} | Projetado CS | Projetado CT |
|:---|---:|---:|---:|
{table_rows}

---

Atenciosamente,
**Equipe [Área]**
"""


# ---------------------------------------------------------------------------
# Phase 6 — English translation (single LLM call)
# ---------------------------------------------------------------------------

_TRANSLATION_SYSTEM = """\
You are a financial translator specialising in Brazilian credit committee documents.
Translate the Portuguese credit committee memo provided to fluent English.

Rules:
- Translate all prose and section labels faithfully.
- Keep verbatim: company names, CNPJ, financial figures (R$ amounts, percentages,
  multiples), account codes, proper nouns, and the company's own disclosed metric
  labels (e.g. "LAJIDA ajustado").
- Translate key template labels as follows:
    Faturamento → Revenue
    Alavancagem → Leverage
    Dívida Líquida → Net Debt
    Margem EBITDA → EBITDA Margin
    Grau de preocupação → Concern Level
    Próximos passos → Next Steps
    Limite → Credit Limit
    Risco → Exposure
    Último comitê → Last committee
    Run-off → Run-off (keep as-is)
    Atenciosamente → Best regards
    Equipe → Team
    Highlights → Highlights
    Perspectivas → Outlook
    Realizado → Actual
    Projetado → Projected
    Indicador → Indicator
- Keep [PREENCHER] placeholders exactly as-is (do not translate them).
- Return only the translated Markdown — no commentary, no preamble.
"""


async def translate_to_en(report_pt_md: str) -> str:
    try:
        return await chat_completion(
            _TRANSLATION_SYSTEM,
            report_pt_md,
            temperature=0.1,
        )
    except Exception as exc:
        return f"[Translation failed: {exc}]\n\n---\n\n{report_pt_md}"
