"""
Synthetic tests for the credit committee 1-pager pipeline.
No LLM calls — uses hard-coded objects to test pure-Python logic only.

Run: python3 test_committee_agents.py
"""

import sys
from datetime import datetime

from app.analysis.committee.schemas import (
    BankContext,
    CitedBullet,
    CommitteeHeaderOutput,
    CommitteeSection,
    CommitteeReport,
    FinancialTableRow,
)
from app.analysis.committee.composer import (
    _build_financial_table,
    compose_committee_template_pt,
)
from app.analysis.schemas import (
    Citation,
    CompanyDossier,
    DossierCoverage,
    DisclosedMetric,
    FinancialLineItem,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  {PASS}  {name}")
    else:
        msg = f"  {FAIL}  {name}" + (f" — {detail}" if detail else "")
        print(msg)
        _failures.append(name)


def _cite() -> Citation:
    return Citation(document_id="test", filename="test.csv", section=None)


def _make_dossier(
    with_3_01: bool = True,
    with_ebitda: bool = True,
    with_margem: bool = True,
    with_dl: bool = True,
    with_alav: bool = True,
) -> CompanyDossier:
    line_items = []
    metrics = []

    if with_3_01:
        line_items.append(
            FinancialLineItem(
                account_code="3.01",
                description="Receita de Venda de Bens e/ou Serviços",
                # cvm_client stores raw R$ (MIL × 1 000): 50 MM = 50 000 000 raw R$
                value=50_000_000.0,
                scale="MIL",
                period_label="FY 2024",
                statement_type="DRE_con",
                citation=_cite(),
            )
        )
        # Also add an older period to test "most recent" selection
        line_items.append(
            FinancialLineItem(
                account_code="3.01",
                description="Receita de Venda de Bens e/ou Serviços",
                value=45_000_000.0,  # 45 MM = 45 000 000 raw R$
                scale="MIL",
                period_label="FY 2023",
                statement_type="DRE_con",
                citation=_cite(),
            )
        )

    if with_ebitda:
        metrics.append(
            DisclosedMetric(
                label="EBITDA Ajustado",
                value=12_500.0,
                unit="R$ milhões",
                period_label="FY 2024",
                definition=None,
                citation=_cite(),
            )
        )

    if with_margem:
        metrics.append(
            DisclosedMetric(
                label="Margem EBITDA",
                value=25.0,
                unit="%",
                period_label="FY 2024",
                definition=None,
                citation=_cite(),
            )
        )

    if with_dl:
        metrics.append(
            DisclosedMetric(
                label="Dívida Líquida",
                value=30_000.0,
                unit="R$ milhões",
                period_label="FY 2024",
                definition=None,
                citation=_cite(),
            )
        )

    if with_alav:
        metrics.append(
            DisclosedMetric(
                label="Alavancagem (DL/EBITDA)",
                value=2.4,
                unit="x",
                period_label="FY 2024",
                definition=None,
                citation=_cite(),
            )
        )

    return CompanyDossier(
        cnpj="33.592.510/0001-54",
        cd_cvm="9512",
        name="VALE S.A.",
        trade_name="Vale",
        sector="Mineração",
        generated_at=datetime.now(),
        financial_line_items=line_items,
        disclosed_metrics=metrics,
        qualitative_facts=[],
        conflicts=[],
        coverage=DossierCoverage(
            dfp_years=[2024, 2023],
            itr_years=[],
            fre_years=[2024],
            fre_sections_present=["2.1", "4.1"],
            fre_sections_missing=["6.5"],
        ),
    )


def _hard_coded_header() -> CommitteeHeaderOutput:
    return CommitteeHeaderOutput(
        framing_paragraph="**Vale registrou receita de R$ 50 MM em 2024, com EBITDA de R$ 12.5 MM e margem de 25%.**",
        grau_preocupacao="Médio",
        grau_preocupacao_reasoning="Alavancagem de 2.4x é moderada para o setor de mineração.",
        proximos_passos="Monitorar redução de alavancagem; acompanhar vencimentos de dívida em 2026.",
        extracted_ratings={"cs": "BB+"},
    )


def _hard_coded_section(section_id: str, year: str = "2024") -> CommitteeSection:
    return CommitteeSection(
        section_id=section_id,
        year_label=year,
        bullets=[
            CitedBullet(
                text="Receita cresceu no período, impulsionada por volume de minério de ferro.",
                source="Conta 3.01 — DRE_con — FY 2024",
            ),
            CitedBullet(
                text="EBITDA ajustado atingiu R$ 12.500 MM com margem de 25%.",
                source="Métrica divulgada — EBITDA Ajustado — FY 2024",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Test: _build_financial_table
# ---------------------------------------------------------------------------

def test_financial_table_structure():
    print("\n--- Financial table builder ---")
    dossier = _make_dossier()
    bank_ctx = BankContext()
    table = _build_financial_table(dossier, bank_ctx)

    check("returns 7 rows", len(table) == 7, f"got {len(table)}")

    indicators = [r.indicator for r in table]
    check("row 0 is Faturamento", "Faturamento" in indicators[0])
    check("row 1 is EBITDA", "EBITDA" in indicators[1])
    check("row 2 is Margem EBITDA", "Margem" in indicators[2])
    check("row 3 is Lucro Líquido", "Lucro" in indicators[3])
    check("row 4 is Caixa Operacional", "Caixa" in indicators[4])
    check("row 5 is Dívida Líquida", "Dívida" in indicators[5])
    check("row 6 is Alavancagem", "Alavancagem" in indicators[6])

    fat_row = table[0]
    check("Faturamento realizado not '—'", fat_row.realizado != "—", fat_row.realizado)
    # 50,000 MIL = 50 MM
    check("Faturamento ~50.0 MM", "50.0" in fat_row.realizado, fat_row.realizado)

    ebitda_row = table[1]
    check("EBITDA realizado not '—'", ebitda_row.realizado != "—", ebitda_row.realizado)

    margem_row = table[2]
    check("Margem realizado has '%'", "%" in margem_row.realizado or margem_row.realizado != "—")

    dl_row = table[5]
    check("DL realizado not '—'", dl_row.realizado != "—", dl_row.realizado)

    alav_row = table[6]
    check("Alavancagem realizado has 'x'", "x" in alav_row.realizado, alav_row.realizado)

    # All projetado fields should be [PREENCHER] when BankContext is empty
    for row in table:
        check(f"{row.indicator}: projetado_cs is [PREENCHER]", row.projetado_cs == "[PREENCHER]")
        check(f"{row.indicator}: projetado_ct is [PREENCHER]", row.projetado_ct == "[PREENCHER]")


def test_financial_table_missing_data():
    print("\n--- Financial table: missing data ---")
    dossier = _make_dossier(with_3_01=False, with_ebitda=False, with_margem=False, with_dl=False, with_alav=False)
    bank_ctx = BankContext()
    table = _build_financial_table(dossier, bank_ctx)
    check("still 7 rows when all data missing", len(table) == 7)
    for row in table:
        check(f"{row.indicator}: realizado is '—' when missing", row.realizado == "—")


def test_financial_table_with_bank_context():
    print("\n--- Financial table: with BankContext projections ---")
    dossier = _make_dossier()
    bank_ctx = BankContext(
        projetado_cs={"faturamento": 55.0, "ebitda": 14.0, "margem_ebitda": 25.5, "divida_liquida": 28.0, "alavancagem": 2.0},
        projetado_ct={"faturamento": 52.0, "ebitda": 13.0, "margem_ebitda": 25.0, "divida_liquida": 29.0, "alavancagem": 2.2},
    )
    table = _build_financial_table(dossier, bank_ctx)
    fat = table[0]
    check("projetado_cs filled for faturamento", fat.projetado_cs == "55.0", fat.projetado_cs)
    check("projetado_ct filled for faturamento", fat.projetado_ct == "52.0", fat.projetado_ct)


# ---------------------------------------------------------------------------
# Test: compose_committee_template_pt
# ---------------------------------------------------------------------------

def test_compose_pt_template():
    print("\n--- PT template composer ---")
    dossier = _make_dossier()
    header = _hard_coded_header()
    consolidado = _hard_coded_section("highlights_consolidado")
    holding = _hard_coded_section("highlights_holding")
    perspectivas = _hard_coded_section("perspectivas", year="2025")
    bank_ctx = BankContext()
    table = _build_financial_table(dossier, bank_ctx)

    md = compose_committee_template_pt(dossier, header, consolidado, holding, perspectivas, table, bank_ctx)

    check("contains company name", "Vale" in md)
    check("contains Resultados", "Resultados" in md)
    check("section 1 Highlights present", "Highlights" in md and "Consolidado" in md)
    check("section 2 Holding present", "Holding" in md)
    check("section 3 Perspectivas present", "Perspectivas" in md)
    check("financial table header present", "Indicador" in md)
    check("table has 3 data columns", md.count("| Projetado") >= 2)
    check("grau de preocupação present", "Grau de preocupação" in md or "preocupação" in md)
    check("próximos passos present", "Próximos passos" in md or "passos" in md)
    check("framing paragraph present", "Vale registrou" in md or "50 MM" in md)

    # All None BankContext fields should render as [PREENCHER]
    check("[PREENCHER] appears for Limite", "[PREENCHER]" in md)
    preencher_count = md.count("[PREENCHER]")
    check("multiple [PREENCHER] placeholders", preencher_count >= 4, f"found {preencher_count}")

    # Ratings: extracted_ratings has cs=BB+, so rating_cs should be BB+
    check("extracted rating CS appears", "BB+" in md)

    check("Atenciosamente at end", "Atenciosamente" in md)


def test_compose_with_bank_context_filled():
    print("\n--- PT template: filled BankContext ---")
    dossier = _make_dossier()
    header = _hard_coded_header()
    consolidado = _hard_coded_section("highlights_consolidado")
    holding = _hard_coded_section("highlights_holding")
    perspectivas = _hard_coded_section("perspectivas", year="2025")
    bank_ctx = BankContext(
        limite_mm=500.0,
        risco_mm=120.0,
        run_off_pct=95.0,
        run_off_assets=["Debêntures Série 1", "CRA"],
        run_off_holding_pct=60.0,
        share_banco_name="Itaú",
        share_banco_pct=12.5,
        rating_cs="BB+",
        rating_holding="BB",
        rating_ativos_maduros="BBB-",
        ultimo_comite="Mar/2025",
    )
    table = _build_financial_table(dossier, bank_ctx)
    md = compose_committee_template_pt(dossier, header, consolidado, holding, perspectivas, table, bank_ctx)

    check("Limite filled (not PREENCHER)", "500" in md and "Limite" in md)
    check("Risco filled", "120" in md)
    check("Share Itaú filled", "Itaú" in md)
    check("Último comitê filled", "Mar/2025" in md)
    # Projections (projetado_cs/ct) are NOT set → 10 table cells still [PREENCHER].
    # Header fields are all filled → no [PREENCHER] in the header block.
    header_block = md.split("Highlights")[0]
    check("No [PREENCHER] in header when all header fields filled", "[PREENCHER]" not in header_block)


# ---------------------------------------------------------------------------
# Test: CommitteeReport schema round-trip
# ---------------------------------------------------------------------------

def test_schema_roundtrip():
    print("\n--- Schema round-trip ---")
    dossier = _make_dossier()
    bank_ctx = BankContext(limite_mm=500.0)
    table = _build_financial_table(dossier, bank_ctx)
    header = _hard_coded_header()
    consolidado = _hard_coded_section("highlights_consolidado")
    holding = _hard_coded_section("highlights_holding")
    perspectivas = _hard_coded_section("perspectivas", year="2025")

    report = CommitteeReport(
        cnpj=dossier.cnpj,
        name=dossier.name,
        trade_name=dossier.trade_name,
        period_label="FY 2024",
        generated_at=datetime.now(),
        bank_context=bank_ctx,
        header_output=header,
        highlights_consolidado=consolidado,
        highlights_holding=holding,
        perspectivas=perspectivas,
        financial_table=table,
        report_pt_md="# Test PT",
        report_en_md="# Test EN",
    )

    json_str = report.model_dump_json()
    restored = CommitteeReport.model_validate_json(json_str)

    check("cnpj survives round-trip", restored.cnpj == report.cnpj)
    check("bank_context.limite_mm survives", restored.bank_context.limite_mm == 500.0)
    check("financial_table length survives", len(restored.financial_table) == 7)
    check("grau_preocupacao survives", restored.header_output.grau_preocupacao == "Médio")


# ---------------------------------------------------------------------------
# Test: build_fact_sheet
# ---------------------------------------------------------------------------


def test_build_fact_sheet():
    from app.analysis.committee.fact_sheet import build_fact_sheet, format_fact_sheet

    print("\n--- build_fact_sheet: full dossier ---")
    dossier = _make_dossier()
    fs = build_fact_sheet(dossier)

    # Periods
    check("period_latest is FY 2024", fs.period_latest == "FY 2024", fs.period_latest)
    check("period_prior is FY 2023", fs.period_prior == "FY 2023", fs.period_prior)

    # Revenue: 50,000 MIL → 50 MM; 45,000 MIL → 45 MM
    # 50 000 000 raw R$ / 1 000 000 = 50 MM; 45 000 000 / 1 000 000 = 45 MM
    check("revenue_latest_mm == 50.0", fs.revenue_latest_mm == 50.0, str(fs.revenue_latest_mm))
    check("revenue_prior_mm == 45.0", fs.revenue_prior_mm == 45.0, str(fs.revenue_prior_mm))
    check("revenue_yoy_abs_mm == 5.0", fs.revenue_yoy_abs_mm == 5.0, str(fs.revenue_yoy_abs_mm))
    # 5/45*100 = 11.111...% → rounded to 11.1
    check(
        "revenue_yoy_pct ≈ 11.1%",
        fs.revenue_yoy_pct is not None and abs(fs.revenue_yoy_pct - 11.1) < 0.05,
        str(fs.revenue_yoy_pct),
    )

    # EBITDA: 12,500 (R$ milhões) — already in MM
    check("ebitda_latest_mm == 12500.0", fs.ebitda_latest_mm == 12_500.0, str(fs.ebitda_latest_mm))
    check("ebitda_period_latest is FY 2024", fs.ebitda_period_latest == "FY 2024", str(fs.ebitda_period_latest))

    # Margin: 25% stated
    check("ebitda_margin_latest_pct == 25.0", fs.ebitda_margin_latest_pct == 25.0, str(fs.ebitda_margin_latest_pct))

    # Net debt: 30,000 MM
    check("net_debt_latest_mm == 30000.0", fs.net_debt_latest_mm == 30_000.0, str(fs.net_debt_latest_mm))

    # Leverage: 2.4x (from "Alavancagem (DL/EBITDA)" label contains "DL/EBITDA")
    check("leverage_latest_x == 2.4", fs.leverage_latest_x == 2.4, str(fs.leverage_latest_x))
    check("leverage_label is set", fs.leverage_label is not None, str(fs.leverage_label))

    # format_fact_sheet produces a usable string
    formatted = format_fact_sheet(fs)
    check("formatted contains period", "FY 2024" in formatted)
    check("formatted contains revenue", "50.0" in formatted)
    check("formatted contains yoy pct", "11.1%" in formatted)
    check("formatted contains stated ratio warning", "stated" in formatted.lower())

    print("\n--- build_fact_sheet: empty dossier ---")
    dossier_empty = _make_dossier(
        with_3_01=False, with_ebitda=False, with_margem=False, with_dl=False, with_alav=False
    )
    fs_empty = build_fact_sheet(dossier_empty)
    check("period_latest is N/A when no items", fs_empty.period_latest == "N/A", fs_empty.period_latest)
    check("revenue_latest_mm is None", fs_empty.revenue_latest_mm is None)
    check("revenue_yoy_pct is None", fs_empty.revenue_yoy_pct is None)
    check("leverage_latest_x is None", fs_empty.leverage_latest_x is None)

    print("\n--- build_fact_sheet: only latest year (no prior → no YoY) ---")
    from datetime import datetime
    from app.analysis.schemas import (
        Citation, DossierCoverage, FinancialLineItem, CompanyDossier
    )

    def _cite_local() -> Citation:
        return Citation(document_id="test", filename="test.csv", section=None)

    single_year_dossier = CompanyDossier(
        cnpj="00.000.000/0001-00",
        cd_cvm="0000",
        name="TEST S.A.",
        trade_name="Test",
        sector="Test",
        generated_at=datetime.now(),
        financial_line_items=[
            FinancialLineItem(
                account_code="3.01",
                description="Receita",
                value=100_000_000.0,  # 100 MM = 100 000 000 raw R$
                scale="MIL",
                period_label="FY 2024",
                statement_type="DRE_con",
                citation=_cite_local(),
            )
        ],
        disclosed_metrics=[],
        qualitative_facts=[],
        conflicts=[],
        coverage=DossierCoverage(
            dfp_years=[2024], itr_years=[], fre_years=[],
            fre_sections_present=[], fre_sections_missing=[],
        ),
    )
    fs_single = build_fact_sheet(single_year_dossier)
    check("period_latest is FY 2024", fs_single.period_latest == "FY 2024")
    check("period_prior is None (only one FY)", fs_single.period_prior is None)
    check("revenue_yoy_abs_mm is None (no prior)", fs_single.revenue_yoy_abs_mm is None)
    check("revenue_yoy_pct is None (no prior)", fs_single.revenue_yoy_pct is None)
    # 100 000 000 raw R$ / 1 000 000 = 100 MM
    check("revenue_latest_mm == 100.0", fs_single.revenue_latest_mm == 100.0, str(fs_single.revenue_latest_mm))


# ---------------------------------------------------------------------------
# Test: cross_check
# ---------------------------------------------------------------------------


def test_cross_check():
    from app.analysis.committee.fact_sheet import build_fact_sheet
    from app.analysis.committee.verifier import cross_check

    dossier = _make_dossier()
    fs = build_fact_sheet(dossier)

    print("\n--- cross_check: all numbers from dossier ---")
    # Revenue=50 MM, EBITDA=12,500 MM, margin=25%, DL=30,000 MM, leverage=2.4x
    clean_md = (
        "Receita de R$ 50,0 MM em FY 2024, com EBITDA Ajustado de R$ 12.500 MM "
        "e margem de 25%. Dívida Líquida de R$ 30.000 MM e alavancagem de 2,4x."
    )
    r = cross_check(clean_md, fs, dossier)
    check("no unverified numbers in clean md", len(r.unverified) == 0, str(r.unverified))
    check("some numbers verified", r.verified > 0, str(r.verified))
    check("warning is None when clean", r.warning is None)

    print("\n--- cross_check: hallucinated value ---")
    bad_md = (
        "Receita de R$ 50,0 MM em FY 2024. "
        "A empresa atingiu alavancagem de 9,9x, bem acima do esperado."
    )
    r_bad = cross_check(bad_md, fs, dossier)
    check("9,9 flagged as unverified", len(r_bad.unverified) > 0, str(r_bad.unverified))
    check("warning not None when hallucination", r_bad.warning is not None)
    check("warning mentions count", "number" in (r_bad.warning or ""))

    print("\n--- cross_check: billion-scale expression ---")
    # "30 bilhões" should match DL=30,000 MM (30,000/1000 = 30.0 in pool)
    billions_md = "Dívida Líquida de R$ 30 bilhões e margem de 25%."
    r_bil = cross_check(billions_md, fs, dossier)
    check("30 (billions) verified against 30,000 MM", "30" not in r_bil.unverified, str(r_bil.unverified))

    print("\n--- cross_check: empty markdown ---")
    r_empty = cross_check("", fs, dossier)
    check("no results for empty md", r_empty.verified == 0 and len(r_empty.unverified) == 0)
    check("warning is None for empty md", r_empty.warning is None)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("Credit Committee 1-Pager — Synthetic Tests")
    print("=" * 60)

    test_financial_table_structure()
    test_financial_table_missing_data()
    test_financial_table_with_bank_context()
    test_compose_pt_template()
    test_compose_with_bank_context_filled()
    test_schema_roundtrip()
    test_build_fact_sheet()
    test_cross_check()

    print()
    if _failures:
        print(f"  {len(_failures)} test(s) FAILED: {', '.join(_failures)}")
        sys.exit(1)
    else:
        print(f"  All tests passed.")
