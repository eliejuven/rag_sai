"""
Company Dossier schema.

The Dossier is the single, fully-cited source of truth for a company's
financial and qualitative data, built once per CNPJ from everything already
scraped (DFP/ITR line items + FRE qualitative sections). Every section
generator ("agent") in Phase 4 reads the full Dossier rather than touching
storage.chunks directly.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class Citation(BaseModel):
    document_id: str  # e.g. "fre_<cnpj>_2024" or "cvm_<cnpj>"
    filename: str
    section: str | None = None  # FRE section number, e.g. "4.1"
    section_label: str | None = None
    page_number: int | None = None


class FinancialLineItem(BaseModel):
    account_code: str
    description: str
    value: float
    scale: str
    period_label: str  # "FY 2024", "ITR 2Q2025", etc.
    statement_type: str  # DRE_con, BPA_con, BPP_con, DFC_*_con
    citation: Citation


class DisclosedMetric(BaseModel):
    """Company-reported non-GAAP figure, e.g. 'Adjusted EBITDA'."""

    label: str  # company's own term, verbatim
    value: float | None
    unit: str | None  # "R$ milhões", "%", etc.
    period_label: str
    definition: str | None  # how the company defines it (if stated)
    citation: Citation


class QualitativeFact(BaseModel):
    """One discrete fact/claim extracted from an FRE section."""

    section: str  # "4.1", "1.3", etc.
    section_label: str
    text: str  # the extracted statement, close to verbatim
    citation: Citation


class FactConflict(BaseModel):
    description: str  # e.g. "Net Revenue FY2023 differs between DFP and FRE 2.1"
    values: list[tuple[float, Citation]]


class DossierCoverage(BaseModel):
    dfp_years: list[int]
    itr_years: list[int]
    fre_years: list[int]
    fre_sections_present: list[str]  # e.g. ["1.2","1.3",...]
    fre_sections_missing: list[str]


class ExtractedKPI(BaseModel):
    """One concept-normalized KPI extracted by LLM from dossier data."""
    value: float
    unit: str | None = None       # as stated in the source: "R$ milhões", "%", "x", etc.
    label: str                    # verbatim label from the document
    period: str | None = None     # e.g. "FY 2025", "31.12.2024"


class ExtractedKPIs(BaseModel):
    """
    Concept-normalized KPIs for the 1-pager financial table.

    Produced once by extract_financial_kpis() and cached in the dossier JSON.
    Values are stored AS DISCLOSED — unit field describes the scale.
    Callers must normalize to R$ MM using the unit field.
    """
    ebitda: ExtractedKPI | None = None
    ebitda_margin: ExtractedKPI | None = None
    net_debt: ExtractedKPI | None = None
    leverage: ExtractedKPI | None = None


class CompanyDossier(BaseModel):
    cnpj: str
    cd_cvm: str
    name: str
    trade_name: str
    sector: str | None = None  # from CVM registry
    generated_at: datetime
    financial_line_items: list[FinancialLineItem]
    disclosed_metrics: list[DisclosedMetric]
    qualitative_facts: list[QualitativeFact]
    conflicts: list[FactConflict]
    coverage: DossierCoverage
    extracted_kpis: ExtractedKPIs | None = None  # populated by extract_financial_kpis()


class TaggedStatement(BaseModel):
    """One statement produced by a Phase 4 section generator ("agent").

    - "fact": directly stated in the Dossier — citations non-empty.
    - "inference": derived via logic/arithmetic from facts — derived_from
      references the facts combined.
    - "judgment": requires the sector playbook — basis references which part
      of the playbook informed it.
    """

    type: Literal["fact", "inference", "judgment"]
    text: str
    citations: list[Citation] = []
    derived_from: list[str] | None = None
    basis: str | None = None


class SectionOutput(BaseModel):
    section_id: str  # e.g. "business_segments", "debt_capital_structure"
    title: str
    statements: list[TaggedStatement]


class ErrorLogEntry(BaseModel):
    severity: Literal["critical", "warning", "info"]
    stage: Literal["extraction", "calculation", "validation", "generation", "review"]
    message: str
    location: str | None = None  # e.g. "section=credit_metrics, statement_idx=3"


class AnalysisRun(BaseModel):
    cnpj: str
    company_name: str
    generated_at: datetime
    one_pager_md: str
    memo_md: str
    sections: list[SectionOutput]
    limitations: list[str]
    error_log: list[ErrorLogEntry]
    confidence_score: float
    confidence_breakdown: dict[str, float]
