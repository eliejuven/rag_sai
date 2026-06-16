# Credit Committee 1-Pager — Architecture Guide

> **Branch**: `feature/credit-committee-template` (off `main`)
> **Status**: Pre-implementation — this document is the coding contract.
> When implementation starts, tick off each phase as it's completed.
>
> **Objective**: When an analyst types `/1-pager <empresa>` in the chat UI,
> they get a credit committee 1-pager that matches the Itaú internal template —
> auto-filled from CVM/FRE/Yahoo data where possible, with `[PREENCHER]`
> placeholders for internal bank fields the analyst provides. Two language
> versions (Portuguese and English) are generated in one run.

---

## What changes and what stays the same

| Component | Status | Notes |
|---|---|---|
| `app/analysis/sections.py` (9 agents) | **Untouched** | Still powers the full memo |
| `app/analysis/composer.py` | **Untouched** | Still produces `memo.md` |
| `app/analysis/reviewer.py` | **Untouched** | Still powers the full memo |
| `app/analysis/pipeline.py` | **Untouched** | `POST /analysis/generate` unchanged |
| `app/analysis/dossier_builder.py` | **Untouched** | Shared — both pipelines reuse dossier cache |
| `app/scraper/` | **Untouched** | Shared — CVM/FRE/Yahoo scraping unchanged |
| `app/static/index.html` | **One line change** | `/1-pager` command routes to new endpoint |
| `app/main.py` | **One line added** | Register new `committee` router |

Everything new lives under `app/analysis/committee/` and `app/routers/committee.py`.

---

## Data source map (what fills what)

### Header block

| Template field | Source | How |
|---|---|---|
| `[Empresa] \| Resultados [Trimestre/Ano]` | Dossier | `trade_name` + most recent period from `financial_line_items` |
| Limite R$ MM | BankContext (analyst input) | `[PREENCHER]` until provided |
| Risco R$ MM | BankContext (analyst input) | `[PREENCHER]` until provided |
| Run-off % / ativos maduros / % holding | BankContext (analyst input) | `[PREENCHER]` until provided |
| Share [Banco] % | BankContext (analyst input) | `[PREENCHER]` until provided |
| Ratings (CS / Holding / ativos maduros) | FRE text (best-effort extraction by Header agent) + Yahoo metadata | Auto-filled if found, else `[PREENCHER]` |
| Último comitê | BankContext (analyst input) | `[PREENCHER]` until provided |
| **Grau de preocupação** | **LLM-generated** | Header agent synthesizes from FRE 4.1 (risk factors) + sector playbook + Yahoo market data + BCB macro |
| **Próximos passos** | **LLM-generated** | Header agent synthesizes from FRE forward-looking sections + sector context |
| **Framing paragraph** (bold) | **LLM-generated** | Header agent writes 1–2 sentence credit narrative |

### Narrative body

| Section | Source |
|---|---|
| Highlights Consolidado (4–6 bullets) | Highlights Consolidado agent — DRE, DFC, disclosed non-GAAP metrics, FRE 2.x |
| Highlights Holding (3–4 bullets) | Highlights Holding agent — BPA/BPP, FRE governance/ownership, debt structure |
| Perspectivas (3–4 bullets) | Perspectivas agent — FRE 4.1 risk, FRE forward-looking, Yahoo market data |

### Financial table

| Row | Realizado | Projetado CS | Projetado CT |
|---|---|---|---|
| Faturamento (R$ MM) | DRE account 3.01 | BankContext | BankContext |
| EBITDA (R$ MM) | `disclosed_metrics` (label ∋ LAJIDA/EBITDA) | BankContext | BankContext |
| Margem EBITDA (%) | Disclosed or Faturamento/EBITDA calc | BankContext | BankContext |
| Dívida Líquida (R$ MM) | `disclosed_metrics` (label ∋ Dívida Líquida) | BankContext | BankContext |
| Alavancagem (x) | `disclosed_metrics` (label ∋ Alavancagem/Leverage) | BankContext | BankContext |

If a value can't be extracted, the cell shows `—`.

### Analyst note enrichment (already works — no new code)

Analyst uploads a PDF or internal note via `POST /ingest` → gets chunked and embedded into the vector store. The Header and Perspectivas agents receive fallback-search results from the existing store, so uploaded notes automatically enrich the LLM's assessment. No new code needed for this.

---

## New files to create

```
app/analysis/committee/
    __init__.py
    schemas.py       ← BankContext, CommitteeReport, CommitteeHeaderOutput, FinancialTableRow
    agents.py        ← 4 agent functions (Header, Consolidado, Holding, Perspectivas)
    composer.py      ← compose_committee_template_pt(), translate_to_en()
    pipeline.py      ← generate_committee_report() orchestrator

app/routers/
    committee.py     ← POST /committee/generate/stream, POST /committee/generate,
                       GET /committee/{cnpj}, GET /committee/{cnpj}/report.pdf,
                       PUT/GET /committee/{cnpj}/bank-context

data/committee_reports/    ← created at runtime
    .gitkeep
    {cnpj}/
        bank_context.json   ← persisted BankContext per company
        latest/
            report_pt.md
            report_en.md
            report.json     ← full CommitteeReport serialized

test_committee_agents.py   ← synthetic test, no LLM calls
test_committee_pipeline.py ← e2e test against a cached dossier
```

---

## Phase 1 — Schemas (`app/analysis/committee/schemas.py`)

```python
from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel

class BankContext(BaseModel):
    # Analyst-provided fields — all Optional, default None
    limite_mm: float | None = None
    risco_mm: float | None = None
    run_off_pct: float | None = None          # % of exposure in mature assets
    run_off_assets: list[str] = []            # ["Ativo 1", "Ativo 2", "Ativo 3"]
    run_off_holding_pct: float | None = None  # % in holding
    share_banco_name: str | None = None       # "Itaú", "Bradesco", etc.
    share_banco_pct: float | None = None
    rating_cs: str | None = None              # e.g. "BB+" (consolidated)
    rating_holding: str | None = None
    rating_ativos_maduros: str | None = None
    ultimo_comite: str | None = None          # e.g. "Mar/2025"
    # Bank's own projections — dicts keyed by metric name
    projetado_cs: dict[str, float] | None = None
    projetado_ct: dict[str, float] | None = None
    # Updated at
    updated_at: datetime | None = None

class CommitteeHeaderOutput(BaseModel):
    framing_paragraph: str
    grau_preocupacao: str           # "Baixo" | "Médio" | "Alto" | "Muito Alto"
    grau_preocupacao_reasoning: str # 1–2 sentences explaining the level
    proximos_passos: str            # Action items paragraph
    extracted_ratings: dict[str, str] = {}  # best-effort from FRE/Yahoo

class CommitteeSection(BaseModel):
    section_id: str
    year_label: str          # e.g. "2024" or "Q4 2024"
    bullets: list[str]

class FinancialTableRow(BaseModel):
    indicator: str           # "Faturamento (R$ MM)"
    indicator_en: str        # "Revenue (R$ MM)"
    realizado: str           # formatted value or "—"
    projetado_cs: str        # formatted or "—"
    projetado_ct: str        # formatted or "—"

class CommitteeReport(BaseModel):
    cnpj: str
    name: str
    trade_name: str
    period_label: str        # "FY 2024" or "Q3 2025" — most recent period in dossier
    generated_at: datetime
    bank_context: BankContext
    header_output: CommitteeHeaderOutput
    highlights_consolidado: CommitteeSection
    highlights_holding: CommitteeSection
    perspectivas: CommitteeSection
    financial_table: list[FinancialTableRow]
    report_pt_md: str
    report_en_md: str
```

---

## Phase 2 — BankContext persistence

Two helper functions in `app/analysis/committee/pipeline.py` (or a small `persistence.py`):

```python
BANK_CONTEXT_DIR = Path("data/committee_reports")

def load_bank_context(cnpj: str) -> BankContext:
    path = BANK_CONTEXT_DIR / cnpj / "bank_context.json"
    if path.exists():
        return BankContext.model_validate_json(path.read_text())
    return BankContext()  # all-None defaults

def save_bank_context(cnpj: str, ctx: BankContext) -> None:
    path = BANK_CONTEXT_DIR / cnpj / "bank_context.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ctx.model_dump_json(indent=2))
```

When `/1-pager` runs: load from cache, merge with any analyst-supplied overrides, run pipeline, save back.

---

## Phase 3 — Financial table builder

Function `_build_financial_table(dossier, bank_context)` in `composer.py`:

1. **Faturamento**: find first `FinancialLineItem` where `account_code == "3.01"` and `statement_type == "DRE_con"`, most recent `period_label`. Format as R$ MM (value is stored in MIL scale → divide by 1000).
2. **EBITDA**: find first `DisclosedMetric` where `label` contains "LAJIDA" or "EBITDA" (case-insensitive), most recent period.
3. **Margem EBITDA**: if a `DisclosedMetric` with "Margem" + "EBITDA"/"LAJIDA" exists, use it. Else: (EBITDA / Faturamento) × 100 if both found.
4. **Dívida Líquida**: `DisclosedMetric` where label contains "Dívida Líquida" or "Dívida liquida" or "Net Debt".
5. **Alavancagem**: `DisclosedMetric` where label contains "Alavancagem" or "Leverage" or "DL/EBITDA".
6. Projetado columns: from `bank_context.projetado_cs` / `bank_context.projetado_ct` keyed by `"faturamento"`, `"ebitda"`, `"margem_ebitda"`, `"divida_liquida"`, `"alavancagem"`. Format as string or `"—"` if absent.

---

## Phase 4 — The 4 agents (`app/analysis/committee/agents.py`)

All agents follow the same pattern as the existing 9 agents: build a system prompt + user message, call `chat_completion()`, parse JSON response. Rate-limit safe: run sequentially with `_INTER_AGENT_DELAY = 2.0` seconds.

### Agent 1 — Header Agent

**System prompt (key parts)**:
- Role: senior credit analyst writing a credit committee header for an internal Itaú-style 1-pager
- Input: full Dossier (financial statements, disclosed metrics, qualitative FRE facts), Yahoo market snapshot if available, BCB macro snapshot
- Task: produce (1) a 1–2 sentence bold framing paragraph capturing the credit story of the most recent year; (2) a "Grau de preocupação" level (Baixo/Médio/Alto/Muito Alto) with 1–2 sentences of reasoning; (3) a "Próximos passos" action paragraph
- Also attempt: scan the qualitative FRE text for any mentions of external ratings (S&P, Fitch, Moody's) per entity — if found, include in `extracted_ratings`
- Generate in **Portuguese**
- Language of disclosed labels (e.g. "LAJIDA ajustado") preserved verbatim

**JSON output schema**:
```json
{
  "framing_paragraph": "...",
  "grau_preocupacao": "Médio",
  "grau_preocupacao_reasoning": "...",
  "proximos_passos": "...",
  "extracted_ratings": {"cs": "...", "holding": "...", "ativos_maduros": "..."}
}
```

### Agent 2 — Highlights Consolidado

**Task**: write 4–6 concise bullet points on the current year's consolidated operational and financial performance.

**Focus FRE sections**: 2.1 (business overview), 10.1 (discussion of results) if present, plus DRE + DFC financial statements + disclosed non-GAAP.

**Guidance**: each bullet should be a specific, quantified insight — revenue growth driver (YoY %, mix breakdown), cost items that moved, EBITDA impact, Capex trend, financial expenses, leverage at year-end. Avoid vague statements ("results were in line") — be specific ("Revenue grew X% YoY, of which Y p.p. from tariff increases").

**JSON output schema**:
```json
{
  "year_label": "2024",
  "bullets": ["...", "...", "...", "...", "..."]
}
```

### Agent 3 — Highlights Holding

**Task**: write 3–4 bullet points on the holding company view — dividend upstream flows, support to subsidiaries, intercompany dynamics, holding-level leverage.

**Focus FRE sections**: 6.5 (ownership/group structure), 7.1 (governance), BPA/BPP for holding vs consolidated delta.

**Edge case**: if the company has no holding structure (sole legal entity, no subsidiaries), the agent writes `{"year_label": "...", "bullets": ["Not applicable — company has a single-entity structure with no holding layer."]}` and the composer renders a note instead of bullets.

**JSON output schema**: same shape as Agent 2.

### Agent 4 — Perspectivas

**Task**: write 3–4 forward-looking bullet points: deleveraging trajectory, new concessions/M&A plans, asset sales, debt rollover schedule.

**Focus FRE sections**: 4.1 (risk factors — includes forward-looking risk language), 3.x (expectations for the next period), plus Yahoo market data for market-implied view.

**JSON output schema**: same shape as Agent 2.

---

## Phase 5 — Template composer, Portuguese version

Function `compose_committee_template_pt(dossier, report)` in `composer.py`.

Full rendered Markdown output (the exact template format):

```
**{trade_name} | Resultados {period_label}**

- **Limite:** R$ {limite} MM
- **Risco:** R$ {risco} MM (disponibilidade apenas em cartão de crédito)
  - 100% Run-off: {run_off_pct}% do risco em ativos maduros ({run_off_assets}); {holding_pct}% na holding
  - Share {banco}: {share_pct}%
- **Ratings:** {rating_cs} CS, {rating_holding} Holding, ativos maduros > {rating_maduros}
- **Último comitê:** {ultimo_comite}
- **Grau de preocupação:** {grau} ({reasoning})
- **Próximos passos:** {proximos_passos}

{framing_paragraph}

**1. Highlights {year} (Consolidado):** [opening line if needed]

- {bullet 1}
- {bullet 2}
...

**2. Highlights {year} (Holding):** [opening line if needed]

- {bullet 1}
...

**3. Perspectivas {year+1}:** tendência positiva

- {bullet 1}
...

---

**Realizado {year} vs projetado {banco} (CS e CT):**

**Informações financeiras:**

| Indicador | Realizado {year} | Projetado CS | Projetado CT |
|:---|---:|---:|---:|
| Faturamento (R$ MM) | {v} | {cs} | {ct} |
| EBITDA (R$ MM) | {v} | {cs} | {ct} |
| Margem EBITDA (%) | {v} | {cs} | {ct} |
| Dívida Líquida (R$ MM) | {v} | {cs} | {ct} |
| Alavancagem (x) | {v} | {cs} | {ct} |

---

Atenciosamente,
**Equipe [Área]**
```

**Placeholder rules**: any field where the value is `None` renders as `[PREENCHER]`. The analyst can search-replace these after downloading the document.

---

## Phase 6 — English version (one translation call)

Function `translate_to_en(report_pt_md, dossier)` in `composer.py`.

Single `chat_completion()` call with a tight translation prompt:

```
You are a financial translator. Translate the following Portuguese credit committee
memo to English. Rules:
- Translate all prose and labels faithfully.
- Keep verbatim: company names, CNPJ, financial figures (R$ amounts, percentages,
  multiples), account codes, proper nouns, and the company's own disclosed metric
  labels (e.g. "LAJIDA ajustado").
- Translate table headers and row labels (e.g. "Faturamento" → "Revenue",
  "Alavancagem" → "Leverage", "Dívida Líquida" → "Net Debt",
  "Grau de preocupação" → "Concern Level", "Próximos passos" → "Next Steps",
  "Limite" → "Credit Limit", "Risco" → "Exposure", "Último comitê" → "Last committee",
  "Atenciosamente" → "Best regards", "Equipe" → "Team").
- Keep [PREENCHER] placeholders as-is (do not translate them).
- Return only the translated Markdown, no commentary.
```

This keeps translation cost minimal (1 call) and quality high (the LLM translates naturally rather than label-by-label).

---

## Phase 7 — Pipeline orchestrator (`app/analysis/committee/pipeline.py`)

```python
async def generate_committee_report(
    company_name: str,
    emit: Callable[[str, str], Awaitable[None]],
    bank_context_override: BankContext | None = None,
) -> CommitteeReport:

    # Step 1: Resolve company + build/load dossier (same as Pipeline A)
    await emit("progress", f"Searching for '{company_name}' in CVM registry...")
    dossier = await build_or_load_dossier(company_name, emit)

    # Step 2: Fetch Yahoo market data (best-effort, non-blocking on failure)
    await emit("progress", "Fetching market data...")
    market_ctx = await _fetch_market_context(dossier.trade_name)

    # Step 3: Load + merge BankContext
    bank_context = load_bank_context(dossier.cnpj)
    if bank_context_override:
        bank_context = bank_context.model_copy(
            update=bank_context_override.model_dump(exclude_none=True)
        )

    # Step 4: Run Header agent
    await emit("progress", "Generating header & credit opinion...")
    header_output = await run_header_agent(dossier, market_ctx, bank_context)
    await asyncio.sleep(_INTER_AGENT_DELAY)

    # Step 5: Highlights Consolidado
    await emit("progress", "Generating highlights — consolidated...")
    consolidado = await run_consolidado_agent(dossier)
    await asyncio.sleep(_INTER_AGENT_DELAY)

    # Step 6: Highlights Holding
    await emit("progress", "Generating highlights — holding...")
    holding = await run_holding_agent(dossier)
    await asyncio.sleep(_INTER_AGENT_DELAY)

    # Step 7: Perspectivas
    await emit("progress", "Generating outlook...")
    perspectivas = await run_perspectivas_agent(dossier, market_ctx)
    await asyncio.sleep(_INTER_AGENT_DELAY)

    # Step 8: Financial table
    financial_table = _build_financial_table(dossier, bank_context)

    # Step 9: Compose Portuguese template
    await emit("progress", "Composing Portuguese template...")
    report_pt = compose_committee_template_pt(dossier, header_output, consolidado, holding, perspectivas, financial_table, bank_context)

    # Step 10: Translate to English
    await emit("progress", "Translating to English...")
    report_en = await translate_to_en(report_pt)

    # Step 11: Persist
    report = CommitteeReport(
        cnpj=dossier.cnpj, name=dossier.name, trade_name=dossier.trade_name,
        period_label=_infer_period_label(dossier),
        generated_at=datetime.now(),
        bank_context=bank_context,
        header_output=header_output,
        highlights_consolidado=consolidado,
        highlights_holding=holding,
        perspectivas=perspectivas,
        financial_table=financial_table,
        report_pt_md=report_pt,
        report_en_md=report_en,
    )
    save_committee_report(report)
    save_bank_context(dossier.cnpj, bank_context)

    await emit("result", report.model_dump_json())
    return report
```

Total LLM calls per run: **5** (Header + Consolidado + Holding + Perspectivas + Translation). Runtime target: ~2–4 min (4 agents × ~30s each + 1 translation call × ~20s).

---

## Phase 8 — Router (`app/routers/committee.py`)

```python
POST   /committee/generate/stream   # SSE — main entry point for /1-pager UI command
POST   /committee/generate          # Synchronous version
GET    /committee/{cnpj}            # Load latest CommitteeReport (JSON)
GET    /committee/{cnpj}/report.pdf # Serve PDF (?lang=pt (default) or ?lang=en)
GET    /committee/{cnpj}/bank-context      # Return stored BankContext
PUT    /committee/{cnpj}/bank-context      # Update stored BankContext (analyst fills fields)
```

`/committee/generate/stream` request body:
```json
{
  "company_name": "Vale",
  "bank_context": { ... }   // optional overrides, merged with cached BankContext
}
```

PDF rendering: reuse `app/analysis/pdf_export.py` unchanged — `render_markdown_to_pdf(report_pt_md)` or `render_markdown_to_pdf(report_en_md)`.

---

## Phase 9 — Register router (`app/main.py`)

One line added alongside existing `analysis.router`:
```python
from app.routers import committee
app.include_router(committee.router, prefix="/committee", tags=["committee"])
```

---

## Phase 10 — UI change (`app/static/index.html`)

Find the existing `/1-pager` handler (currently routes to `/analysis/generate/stream`). Change the endpoint URL to `/committee/generate/stream`. The SSE event types are identical (`progress`, `result`, `error`) so no other UI logic changes.

Optionally: on the `result` event, parse `committee_pt_md` (or `committee_en_md` if a `lang=en` flag is set) and render it. The PDF download chip links to `/committee/{cnpj}/report.pdf`.

---

## Phase 11 — Test scripts

### `test_committee_agents.py` (no LLM, synthetic data)
- Build a minimal `CompanyDossier` with synthetic `financial_line_items` and `disclosed_metrics`
- Call `_build_financial_table(dossier, BankContext())` → assert 5 rows, correct metric extraction
- Call `compose_committee_template_pt(...)` with hard-coded agent outputs → assert key strings present in output
- Assert `[PREENCHER]` appears for all None BankContext fields
- Assert table Markdown has 3 data columns

### `test_committee_pipeline.py` (LLM, uses cached dossier)
- Run `generate_committee_report("Vale", emit=...)` against the cached Vale dossier
- Assert `report.report_pt_md` contains the company name, the financial table, and the 3 sections
- Assert `report.report_en_md` contains "Revenue", "Leverage", "Net Debt" (translation confirmed)
- Assert `report.financial_table[0].realizado != "—"` (Faturamento found)
- Assert `report.header_output.grau_preocupacao` in {"Baixo", "Médio", "Alto", "Muito Alto"}

---

## Implementation order

| # | Phase | File(s) | Est. effort |
|---|---|---|---|
| 1 | Schemas | `committee/schemas.py` | Small |
| 2 | BankContext persistence | `committee/pipeline.py` | Small |
| 3 | Financial table builder | `committee/composer.py` | Medium |
| 4 | 4 agents | `committee/agents.py` | Medium |
| 5 | PT template composer | `committee/composer.py` | Medium |
| 6 | EN translation call | `committee/composer.py` | Small |
| 7 | Pipeline orchestrator | `committee/pipeline.py` | Medium |
| 8 | Router + endpoints | `routers/committee.py` | Medium |
| 9 | `app/main.py` | `main.py` | Trivial |
| 10 | UI `/1-pager` reroute | `static/index.html` | Trivial |
| 11 | Test scripts | `test_committee_*.py` | Medium |

**Total new code estimate**: ~700–900 lines across 5 new files + 3 trivial edits. Zero changes to any existing Pipeline A code.

---

## Key design decisions recorded here

- **4 agents, not 9**: purpose-built for the template's 3 narrative sections + header. Less total context per agent = faster, cheaper, more focused output.
- **Portuguese-native, translate to EN**: one translation call after composition, not two full agent runs. PT is the authoritative version; EN is for analyst understanding.
- **BankContext is always Optional**: pipeline runs immediately with `[PREENCHER]` placeholders. Analyst provides bank context over time via `PUT /committee/{cnpj}/bank-context` or as an override in the request body.
- **Dossier is shared**: `build_or_load_dossier()` already caches per CNPJ — Pipeline B reuses it, no double-scraping.
- **Analyst notes enrich automatically**: documents uploaded via the existing `POST /ingest` flow are in the vector store, which the Header and Perspectivas agents can query via fallback search. No new code needed for this.
- **`CommitteeReport` is a separate schema from `AnalysisRun`**: cleaner, avoids bloating the existing memo schema, separate persistence path under `data/committee_reports/`.
