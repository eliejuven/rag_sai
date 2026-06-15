"""
Test script for Phase 5 — Composer.

Builds a small set of hand-written SectionOutputs (covering fact/inference/
judgment statements, shared + distinct citations, and a section with no
judgment) and renders them with compose_memo() and compose_one_pager(), to
check Markdown structure, fact/inference/judgment grouping, and citation
numbering — without spending an LLM run.

Usage: python3 test_composer.py
"""

from datetime import datetime

from app.analysis.composer import compose_memo, compose_one_pager
from app.analysis.schemas import CompanyDossier, Citation, SectionOutput, TaggedStatement

CIT_A = Citation(document_id="fre_123_2024", filename="fre_123_2024.xml", section="2.5", section_label="Medições não contábeis", page_number=12)
CIT_B = Citation(document_id="cvm_123", filename="dfp_123_2024.csv", section="DRE_con", section_label="DRE consolidado", page_number=None)
CIT_C = Citation(document_id="fre_123_2024", filename="fre_123_2024.xml", section="4.1", section_label="Fatores de risco", page_number=30)


def _dossier() -> CompanyDossier:
    return CompanyDossier(
        cnpj="00.000.000/0001-00",
        cd_cvm="00000",
        name="Empresa Exemplo S.A.",
        trade_name="Exemplo",
        sector="Mineração",
        generated_at=datetime(2026, 6, 15),
        financial_line_items=[],
        disclosed_metrics=[],
        qualitative_facts=[],
        conflicts=[],
        coverage=__import__("app.analysis.schemas", fromlist=["DossierCoverage"]).DossierCoverage(
            dfp_years=[2024], itr_years=[], fre_years=[2024],
            fre_sections_present=["2.5", "4.1"], fre_sections_missing=[],
        ),
    )


def _sections() -> list[SectionOutput]:
    return [
        SectionOutput(
            section_id="financial_performance",
            title="Desempenho Financeiro",
            statements=[
                TaggedStatement(type="fact", text="LAJIDA ajustado de FY2024 foi R$ 80,1 bi.", citations=[CIT_A]),
                TaggedStatement(type="inference", text="Margem EBITDA caiu ~3 p.p. em relação a 2023.", derived_from=["LAJIDA 2024 vs LAJIDA 2023"], citations=[CIT_B]),
                TaggedStatement(type="judgment", text="A queda de margem é administrável dado o ciclo de preços do setor.", basis="Manual Setorial §2"),
            ],
        ),
        SectionOutput(
            section_id="risk_contingencies",
            title="Fatores de Risco e Contingências",
            statements=[
                TaggedStatement(type="fact", text="A companhia cita risco regulatório ambiental como material.", citations=[CIT_C]),
                TaggedStatement(type="fact", text="LAJIDA ajustado de FY2024 foi R$ 80,1 bi.", citations=[CIT_A]),
            ],
        ),
        SectionOutput(
            section_id="limitations_coverage",
            title="Limitações e Cobertura",
            statements=[],
        ),
    ]


def main():
    dossier = _dossier()
    sections = _sections()

    memo = compose_memo(dossier, sections)
    one_pager = compose_one_pager(dossier, sections)

    print("=" * 30, "MEMO", "=" * 30)
    print(memo)
    print("\n" + "=" * 30, "ONE-PAGER", "=" * 30)
    print(one_pager)

    assert "## Desempenho Financeiro" in memo
    assert "**Facts**" in memo and "**Analysis**" in memo and "**Judgment**" in memo
    assert "_(estimate: LAJIDA 2024 vs LAJIDA 2023)_" in memo
    assert "_(basis: Manual Setorial §2)_" in memo
    # CIT_A is shared by two statements across sections -> same [n] both times
    body, _, references = memo.partition("## References")
    assert body.count("[1]") == 2
    assert "[1] fre_123_2024.xml" in references
    assert "## Limitações e Cobertura" in memo  # rendered even with no statements

    assert "**Desempenho Financeiro**: A queda de margem é administrável" in one_pager
    assert "**Fatores de Risco e Contingências**: A companhia cita risco regulatório" in one_pager
    assert "Limitações e Cobertura" not in one_pager  # no statements -> skipped

    print("\nOK")


if __name__ == "__main__":
    main()
