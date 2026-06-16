"""
Synthetic tests for the credit committee 1-pager pipeline.
No LLM calls — uses hard-coded objects to test pure-Python logic only.

Run: python3 test_committee_agents.py
"""

import sys
from datetime import datetime

from app.analysis.committee.schemas import (
    BankContext,
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
                value=50_000.0,  # 50,000 MIL = 50 MM
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
                value=45_000.0,
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
            "Receita cresceu 11% a/a, impulsionada por volume de minério de ferro.",
            "EBITDA ajustado atingiu R$ 12.500 MM com margem de 25%.",
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

    check("returns 5 rows", len(table) == 5, f"got {len(table)}")

    indicators = [r.indicator for r in table]
    check("row 0 is Faturamento", "Faturamento" in indicators[0])
    check("row 1 is EBITDA", "EBITDA" in indicators[1])
    check("row 2 is Margem EBITDA", "Margem" in indicators[2])
    check("row 3 is Dívida Líquida", "Dívida" in indicators[3])
    check("row 4 is Alavancagem", "Alavancagem" in indicators[4])

    fat_row = table[0]
    check("Faturamento realizado not '—'", fat_row.realizado != "—", fat_row.realizado)
    # 50,000 MIL = 50 MM
    check("Faturamento ~50.0 MM", "50.0" in fat_row.realizado, fat_row.realizado)

    ebitda_row = table[1]
    check("EBITDA realizado not '—'", ebitda_row.realizado != "—", ebitda_row.realizado)

    margem_row = table[2]
    check("Margem realizado has '%'", "%" in margem_row.realizado or margem_row.realizado != "—")

    dl_row = table[3]
    check("DL realizado not '—'", dl_row.realizado != "—", dl_row.realizado)

    alav_row = table[4]
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
    check("still 5 rows when all data missing", len(table) == 5)
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
    check("financial_table length survives", len(restored.financial_table) == 5)
    check("grau_preocupacao survives", restored.header_output.grau_preocupacao == "Médio")


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

    print()
    if _failures:
        print(f"  {len(_failures)} test(s) FAILED: {', '.join(_failures)}")
        sys.exit(1)
    else:
        print(f"  All tests passed.")
