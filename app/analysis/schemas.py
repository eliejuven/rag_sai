"""
Company Dossier schema.

The Dossier is the single, fully-cited source of truth for a company's
financial and qualitative data, built once per CNPJ from everything already
scraped (DFP/ITR line items + FRE qualitative sections). Every section
generator ("agent") in Phase 4 reads the full Dossier rather than touching
storage.chunks directly.
"""

from datetime import datetime

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
