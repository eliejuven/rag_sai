"""
Test script for Phase 6 — Review / Critic Agent + Error Log.

Hand-built CompanyDossier + SectionOutputs (no LLM calls, runs in
milliseconds) covering:
- 6.1 citation coverage: missing citations / citations that don't resolve
  to a real Dossier source are flagged as "critical".
- 6.2 internal consistency: "R$ ..." figures are cross-checked against
  financial_line_items/disclosed_metrics, and FactConflict "mention both"
  is checked.
- 6.3 confidence score breakdown (coverage/traceability/consistency/divergence).
- 6.4 AnalysisRun build + persistence round-trip.

Usage: python3 test_reviewer.py
"""

import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from app.analysis.composer import compose_memo, compose_one_pager
from app.analysis.reviewer import (
    _candidate_amounts,
    _extract_currency_amounts,
    build_analysis_run,
    build_limitations,
    check_citation_coverage,
    check_internal_consistency,
    compute_confidence,
    persist_analysis_run,
)
from app.analysis.schemas import (
    AnalysisRun,
    CompanyDossier,
    Citation,
    DisclosedMetric,
    DossierCoverage,
    FactConflict,
    FinancialLineItem,
    SectionOutput,
    TaggedStatement,
)

CIT_A = Citation(document_id="fre_123_2024", filename="fre_123_2024.xml", section="2.5", section_label="Medições não contábeis", page_number=12)
CIT_B = Citation(document_id="cvm_123", filename="dfp_123_2024.csv", section="DRE_con", section_label="DRE consolidado", page_number=None)
CIT_X = Citation(document_id="fre_123_2024", filename="fre_123_2024.xml", section="4.1", section_label="Fatores de risco", page_number=99)


def _dossier(**coverage_overrides) -> CompanyDossier:
    coverage_kwargs = dict(
        dfp_years=[2024],
        itr_years=[2025],
        fre_years=[2025],
        fre_sections_present=["2.5", "4.1"],
        fre_sections_missing=["1.12", "5.3", "6.5", "7.1"],
    )
    coverage_kwargs.update(coverage_overrides)
    coverage = DossierCoverage(**coverage_kwargs)
    return CompanyDossier(
        cnpj="00.000.000/0001-00",
        cd_cvm="00000",
        name="Empresa Exemplo S.A.",
        trade_name="Exemplo",
        sector="Mineração",
        generated_at=datetime(2026, 6, 15),
        financial_line_items=[
            FinancialLineItem(
                account_code="3.01",
                description="Receita de Venda de Bens e/ou Serviços",
                value=206005000000.0,
                scale="MIL",
                period_label="FY 2024",
                statement_type="DRE_con",
                citation=CIT_B,
            )
        ],
        disclosed_metrics=[
            DisclosedMetric(
                label="LAJIDA (EBITDA) ajustado",
                value=80121.0,
                unit="R$ milhões",
                period_label="FY 2024",
                definition=None,
                citation=CIT_A,
            )
        ],
        qualitative_facts=[],
        conflicts=[
            FactConflict(
                description="Receita FY2023 differs between DFP and FRE 2.1",
                values=[(100_000_000.0, CIT_B), (105_000_000.0, CIT_A)],
            )
        ],
        coverage=coverage,
    )


def _sections() -> list[SectionOutput]:
    return [
        SectionOutput(
            section_id="financial_performance",
            title="Desempenho Financeiro",
            statements=[
                # fact, citation resolves, number matches disclosed_metrics (80121 * 1e6)
                TaggedStatement(type="fact", text="LAJIDA ajustado de FY2024 foi R$ 80.121 milhões.", citations=[CIT_A]),
                # fact, citation resolves, number matches financial_line_items (206005 * 1e6)
                TaggedStatement(type="fact", text="Receita líquida de FY2024 foi R$ 206.005 milhões.", citations=[CIT_B]),
                # fact with NO citation -> critical (6.1)
                TaggedStatement(type="fact", text="Patrimônio líquido aumentou no período."),
                # fact with a citation that doesn't resolve to any Dossier source -> critical (6.1)
                TaggedStatement(type="fact", text="Dívida total ficou estável.", citations=[CIT_X]),
                # inference, number doesn't match anything in the Dossier -> warning (6.2)
                TaggedStatement(
                    type="inference",
                    text="Dívida bruta estimada em R$ 999.999 milhões.",
                    derived_from=["estimativa própria"],
                ),
            ],
        ),
        SectionOutput(
            section_id="risk_contingencies",
            title="Fatores de Risco e Contingências",
            statements=[
                # mentions one of the two conflicting values (100M) but not the other (105M)
                TaggedStatement(
                    type="judgment",
                    text="Uma das fontes registrou receita de R$ 100 milhões para FY2023.",
                    basis="Manual Setorial §1",
                ),
            ],
        ),
    ]


def test_number_parsing():
    # single separator + 3-digit tail -> ambiguous, both readings returned
    assert _candidate_amounts("80.121") == [80121.0, 80.121]
    assert _candidate_amounts("50,199") == [50199.0, 50.199]
    # single separator + non-3-digit tail -> unambiguous decimal
    assert _candidate_amounts("42,7") == [42.7]
    assert _candidate_amounts("213,72") == [213.72]
    # multiple separators -> last one is decimal, earlier ones are thousands
    assert _candidate_amounts("1.234.567,89") == [1234567.89]
    # no separator
    assert _candidate_amounts("500") == [500.0]

    amounts = _extract_currency_amounts("LAJIDA ajustado de FY2024 foi R$ 80.121 milhões.")
    assert amounts == [("80.121", "milhões", [80121.0 * 1e6, 80.121 * 1e6])]


def test_citation_coverage():
    dossier = _dossier()
    sections = _sections()
    errors = check_citation_coverage(dossier, sections)

    assert len(errors) == 2
    assert all(e.severity == "critical" for e in errors)
    assert all(e.stage == "generation" for e in errors)
    assert "no citations" in errors[0].message
    assert "Patrimônio líquido" in errors[0].message
    assert "does not resolve to a Dossier source" in errors[1].message
    assert "Dívida total" in errors[1].message


def test_internal_consistency():
    dossier = _dossier()
    sections = _sections()
    errors = check_internal_consistency(dossier, sections)

    assert len(errors) == 2
    assert all(e.severity == "warning" for e in errors)
    assert all(e.stage == "validation" for e in errors)

    mismatch = next(e for e in errors if "999.999" in e.message)
    assert "not found among Dossier" in mismatch.message
    assert "section=financial_performance" in mismatch.location

    conflict = next(e for e in errors if "unresolved Dossier conflict" in e.message)
    assert "1/2 conflicting values" in conflict.message


def test_no_false_positives_on_clean_input():
    """A Dossier + sections with no issues produces an empty error log."""
    dossier = _dossier(fre_sections_missing=[])
    dossier.conflicts = []
    sections = [
        SectionOutput(
            section_id="financial_performance",
            title="Desempenho Financeiro",
            statements=[
                TaggedStatement(type="fact", text="LAJIDA ajustado de FY2024 foi R$ 80.121 milhões.", citations=[CIT_A]),
                TaggedStatement(type="fact", text="Receita líquida de FY2024 foi R$ 206.005 milhões.", citations=[CIT_B]),
            ],
        )
    ]
    assert check_citation_coverage(dossier, sections) == []
    assert check_internal_consistency(dossier, sections) == []


def test_confidence_score():
    dossier = _dossier()
    sections = _sections()
    errors = check_citation_coverage(dossier, sections) + check_internal_consistency(dossier, sections)
    score, breakdown = compute_confidence(dossier, sections, errors)

    # coverage: 2/18 FRE sections present
    assert breakdown["coverage"] == round(2 / 18, 3)
    # traceability: 3/4 "fact" statements have non-empty citations
    assert breakdown["traceability"] == round(3 / 4, 3)
    # consistency: 2 validation warnings / 3 numeric fact|inference statements
    assert breakdown["consistency"] == round(1 - 2 / 3, 3)
    assert breakdown["divergence"] == 1.0
    assert 0.0 <= score <= 1.0
    assert score == round(sum(breakdown.values()) / 4, 3)


def test_limitations():
    dossier = _dossier()
    limitations = build_limitations(dossier)

    assert any("4/18" in l and "1.12" in l for l in limitations)
    assert any("conflict" in l for l in limitations)
    # dfp/itr years present -> no limitation about missing them
    assert not any("DFP" in l for l in limitations)
    assert not any("ITR" in l for l in limitations)


def test_build_and_persist_analysis_run():
    dossier = _dossier()
    sections = _sections()
    memo = compose_memo(dossier, sections)
    one_pager = compose_one_pager(dossier, sections)

    run = build_analysis_run(dossier, sections, one_pager, memo)
    assert run.cnpj == dossier.cnpj
    assert run.memo_md == memo
    assert run.one_pager_md == one_pager
    assert len(run.error_log) == 4  # 2 critical (6.1) + 2 warning (6.2)
    assert len(run.limitations) >= 1
    assert 0.0 <= run.confidence_score <= 1.0

    tmp_dir = tempfile.mkdtemp()
    try:
        out_dir = persist_analysis_run(run, base_dir=tmp_dir)
        assert out_dir.exists()
        assert (out_dir / "one_pager.md").read_text(encoding="utf-8") == one_pager
        assert (out_dir / "memo.md").read_text(encoding="utf-8") == memo

        reloaded = AnalysisRun.model_validate_json((out_dir / "run.json").read_text(encoding="utf-8"))
        assert reloaded.confidence_score == run.confidence_score
        assert len(reloaded.error_log) == len(run.error_log)
        assert str(Path(tmp_dir)) in str(out_dir)
    finally:
        shutil.rmtree(tmp_dir)


if __name__ == "__main__":
    test_number_parsing()
    test_citation_coverage()
    test_internal_consistency()
    test_no_false_positives_on_clean_input()
    test_confidence_score()
    test_limitations()
    test_build_and_persist_analysis_run()
    print("OK")
