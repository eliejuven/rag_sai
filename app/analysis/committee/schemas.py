from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class BankContext(BaseModel):
    limite_mm: float | None = None
    risco_mm: float | None = None
    run_off_pct: float | None = None
    run_off_assets: list[str] = []
    run_off_holding_pct: float | None = None
    share_banco_name: str | None = None
    share_banco_pct: float | None = None
    rating_cs: str | None = None
    rating_holding: str | None = None
    rating_ativos_maduros: str | None = None
    ultimo_comite: str | None = None
    projetado_cs: dict[str, float] | None = None
    projetado_ct: dict[str, float] | None = None
    updated_at: datetime | None = None


class CommitteeHeaderOutput(BaseModel):
    framing_paragraph: str
    grau_preocupacao: str  # "Baixo" | "Médio" | "Alto" | "Muito Alto"
    grau_preocupacao_reasoning: str
    proximos_passos: str
    extracted_ratings: dict[str, str] = {}


class CommitteeSection(BaseModel):
    section_id: str
    year_label: str
    bullets: list[str]


class FinancialTableRow(BaseModel):
    indicator: str
    indicator_en: str
    realizado: str
    projetado_cs: str
    projetado_ct: str


class CommitteeReport(BaseModel):
    cnpj: str
    name: str
    trade_name: str
    period_label: str
    generated_at: datetime
    bank_context: BankContext
    header_output: CommitteeHeaderOutput
    highlights_consolidado: CommitteeSection
    highlights_holding: CommitteeSection
    perspectivas: CommitteeSection
    financial_table: list[FinancialTableRow]
    report_pt_md: str
    report_en_md: str
