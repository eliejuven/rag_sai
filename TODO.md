# TODO — Week 3: First Credit Analysis Pipeline (1-Pager + Full Memo)

> **How to use this document**: This is a living reference, not a locked spec.
> Check items off as they're done, and edit freely as we learn things during
> implementation (especially once the 1-pager / memo templates arrive).
> Claude sessions working on this should read this file first for context on
> *why* each piece exists, not just *what* to build.

---

## Status & Recent Decisions (2026-06-12)

**Done.** The FRE governance work is merged into `main` and pushed
(`cb937fe`). `app/scraper/fre_client.py` now has 4 new sections in
`CREDIT_SECTIONS` / `XML_TAG_TO_SECTION` for the new Governance agent (see
roster in item 5 below):
- `1.12` — Operações societárias (corporate restructurings, M&A)
- `5.3`  — Programa de integridade (anti-corruption/compliance program)
- `6.5`  — Organograma dos acionistas e do grupo econômico (ownership/group structure)
- `7.1`  — Características dos órgãos de administração e conselho fiscal (board/management structure)

`CREDIT_SECTIONS` now has 18 entries (was 14). Validated 18/18 sections
extract correctly across 4 cached companies. **Known limitation**: section
`6.5` is usually an image-based org chart — extracted text is often
near-empty (61-536 chars across the 4 tested companies). Keep it for
coverage/citation purposes but don't expect much qualitative content from it.

**Phase 1 (Company Dossier) is COMPLETE and merged to `main`** (`3117b4a`) —
see the status box at the top of Phase 1 below for full details (files
created/modified, validation results, the 1.1 migration decision).

**Phase 3 (Sector Playbooks) infrastructure is COMPLETE** on branch
`feature/sector-playbooks` (off `main`, not yet merged) — see the status box
at the top of Phase 3 below. The template, loader, and a generic
`_default.md` playbook exist and are wired up; actual sector-specific
playbook content (3.2) is still pending faculty meetings.

**Next**: merge `feature/sector-playbooks` once this session's commits are
in, then start Phase 4 (section generators) — this is the **first phase that
needs the 1-pager/memo template** (see decision 7 and Phase 1/3 status
boxes). Until the template arrives, Phase 4 work can still proceed using the
v1 agent roster (decision 5) as a placeholder structure.

### Architecture refinements agreed since the roadmap below was written
These **supersede** anything in Phases 1-4 below that conflicts:

1. **Full-Dossier-per-agent, not slices.** `build_dossier(cnpj)` returns ONE
   complete `CompanyDossier` (all years of DFP/ITR line items, all 18 FRE
   sections' qualitative facts, all disclosed metrics). Every section
   generator ("agent") receives the **entire** Dossier — specialization
   happens via the agent's prompt ("you are the Debt & Capital Structure
   lens..."), not by restricting its input. This lets, e.g., the Debt agent
   catch a covenant mentioned in FRE 1.15 (Contratos Relevantes) even though
   that's not its "primary" source.
2. **Fallback RAG search, not a tool-calling loop.** If an agent's view of the
   Dossier is thin for what it needs (e.g. its topic is in
   `DossierCoverage.fre_sections_missing`), the agent function can call
   `vector_store.search()` / `bm25_index.search()` directly — same primitives
   `app/query/` already uses — for a few extra chunks, each still carrying a
   `Citation`. Plain function call, not an LLM tool-use loop — keeps with the
   "no agent framework" constraint.
3. **Phase 2 (Calculation Engine) is DEFERRED** — out of scope for v1. Agents
   work from disclosed figures (DFP/ITR line items + FRE 2.5 disclosed
   metrics) only; no standardized EBITDA/ratio computation yet. Phase 2 below
   is kept for reference but should **not** be implemented yet.
4. **Fixed agent roster — sector-awareness lives inside each agent, not in a
   different roster per sector.** One fixed set of ~9 specialized agents
   always runs, for every company. Each agent receives `dossier.sector` + the
   matching sector playbook (Phase 3) and adapts WHAT it looks for / HOW it
   reasons accordingly (e.g. Debt agent: "regulatory capital" framing for
   banks vs. "lease-adjusted leverage" for retailers) — but the agent itself,
   and its slot in the output, never changes. Do **not** build a
   `SECTION_REGISTRY` keyed by sector.
5. **New v1 agent roster** (replaces Phase 4.2's placeholder table — updated
   below): Business & Segments, Financial Performance, Debt & Capital
   Structure, Cash Flow & Liquidity, Risk Factors & Contingencies, Non-GAAP /
   KPIs, **Governance & Ownership Structure (new)**, MIT Outlook (judgment),
   Limitations & Coverage (meta).
6. **Orchestrator (Phase 7) stays a fixed, ordered pipeline for v1** — runs
   all 9 agents in order; later agents (MIT Outlook, Limitations) receive
   earlier agents' `SectionOutput`s as additional context. Dynamic
   per-sector agent selection is a later-week concern, not v1.
7. **Market valuation (Yahoo/yfinance) and BCB macro data are NOT part of
   `CompanyDossier`.** They live in `app/scraper/market_data.py` and
   `app/scraper/bcb_client.py`, fetched live for the `/query` "market"/"macro"
   intents — not cached in `storage.documents` or `data/dossiers/`. They are
   point-in-time snapshots (price, P/E, Selic, IPCA...), fundamentally
   different from the Dossier's stable scraped facts. If a Phase 4 agent
   needs this (e.g. MIT Outlook citing trading multiples or the Selic rate
   when framing leverage), it should call `fetch_market_data()` /
   `get_macro_data()` directly at report-generation time — same "plain
   function call" pattern as fallback RAG search (decision 2), not by
   extending `build_dossier()`. **Open question**: which agent(s) need this
   and how — separate subsection vs. inline framing? Not decided yet.

---

## Table of Contents

1. [Goal & Definition of Done](#0-goal--definition-of-done)
2. [Architecture at a Glance](#1-architecture-at-a-glance)
3. [Data Foundations — What We Can Extract Today](#2-data-foundations--what-we-can-extract-today)
4. [Phase 1 — Company Dossier (Fact Extraction)](#phase-1--company-dossier-fact-extraction-layer)
5. [Phase 2 — Calculation Engine](#phase-2--calculation-engine-standardized-metrics-scoped--discardable)
6. [Phase 3 — Sector Playbooks (MIT Faculty Lens)](#phase-3--sector-playbooks-mit-faculty-lens)
7. [Phase 4 — Tagging + Section Generators](#phase-4--factinferencejudgment-tagging--section-generators)
8. [Phase 5 — Composer (1-Pager + Memo)](#phase-5--composer-1-pager--full-memo)
9. [Phase 6 — Review / Critic + Error Log](#phase-6--review-critic-agent--error-log)
10. [Phase 7 — Orchestration & API](#phase-7--orchestration--api)
11. [Phase 8 — Benchmark Round Prep](#phase-8--benchmark-round-prep)
12. [File / Module Map](#file--module-map)
13. [Open Questions / Decisions Needed](#open-questions--decisions-needed)
14. [Looking Ahead: Weeks 4–7](#looking-ahead-how-this-maps-to-weeks-47)

---

## 0. Goal & Definition of Done

**Program brief for this week:**
> First version of the MIT credit analysis skill; automatic generation of a
> one-page credit snapshot and a full credit memo; separation between facts,
> inferences, and analytical judgment; error-reduction architecture covering
> retrieval, extraction, validation, calculation, generation, and review;
> second external benchmark round with professors, PhDs, EMBAs, or market
> practitioners.
>
> **Outcome**: Preliminary credit analyses generated for selected companies,
> with traceable data, explicit limitations, and a documented error log.

### Definition of done
- [ ] Pipeline can take a company name (+ optional year) and produce both a
      **1-page credit snapshot** and a **full credit memo** as Markdown.
- [ ] Every factual claim/number in the output is traceable to a source
      document + section/page (citation).
- [ ] Output explicitly separates **facts**, **inferences**, and
      **judgment/opinion** internally (display format TBD pending templates).
- [ ] Each generated analysis ships with a **limitations list** (what data
      was missing/unavailable) and an **error log** (conflicts, low-confidence
      extractions, validation failures).
- [ ] Pipeline runs end-to-end on a handful of companies we already have full
      DFP+ITR+FRE coverage for (see [Phase 8](#phase-8--benchmark-round-prep)).
- [ ] At least a draft of the **MIT sector lens** mechanism exists and is
      wired into the "judgment" sections, even if only 1–2 sector playbooks
      exist by end of week.
- [ ] Ready to show output to professors/practitioners for the second
      benchmark round (oral feedback — see Phase 8).

---

## 1. Architecture at a Glance

```
                ┌──────────────────────────────────────────────────────┐
                │              EXISTING RAG PIPELINE                     │
                │  (CVM scrape → chunks/vectors/BM25 → storage.*)        │
                └───────────────────────────┬────────────────────────────┘
                                             │ reads existing
                                             │ storage.documents /
                                             │ storage.chunks for a CNPJ
                                             ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ Phase 1  COMPANY DOSSIER (Fact Extraction)                                 │
│   - Structured financial line items (code-based, no LLM)                  │
│   - LLM-extracted qualitative facts (FRE sections)                        │
│   - LLM-extracted disclosed metrics (FRE 2.5: EBITDA variants, KPIs)      │
│   - Lightweight cross-source conflict flagging                            │
│   → ONE deduplicated JSON artifact per company: data/dossiers/<cnpj>.json │
└───────────────────────────┬───────────────────────────────────────────────┘
                             │
              ┌──────────────┴───────────────┐
              ▼                               ▼
┌──────────────────────────────┐   ┌─────────────────────────────────────┐
│ Phase 2  CALCULATION ENGINE   │   │ Phase 3  SECTOR PLAYBOOKS (MIT lens) │
│  - Standardized ratios from   │   │  - data/playbooks/<sector>.md        │
│    raw line items (pure code) │   │  - Elicited from faculty meetings    │
│  - Flags vs. disclosed values │   │  - Retrieved by company sector       │
└───────────────┬────────────────┘   └───────────────┬───────────────────┘
                │                                    │
                └───────────────┬────────────────────┘
                                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ Phase 4  SECTION GENERATORS                                                │
│   Each section: Dossier subset + computed metrics (+ playbook for         │
│   judgment sections) → tagged statements (fact / inference / judgment),   │
│   each with citations.                                                     │
└───────────────────────────┬───────────────────────────────────────────────┘
                             ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ Phase 5  COMPOSER → 1-Pager (Markdown) + Full Memo (Markdown)              │
└───────────────────────────┬───────────────────────────────────────────────┘
                             ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ Phase 6  REVIEW / CRITIC AGENT                                             │
│   - Citation coverage check, internal consistency check,                  │
│   - confidence score (v0), limitations + error_log.json                   │
└───────────────────────────────────────────────────────────────────────────┘
```

New top-level module: **`app/analysis/`** — sits alongside `app/scraper/`,
`app/query/`, `app/generation/`. It is a **downstream consumer** of the
existing scrape pipeline: it never scrapes anything itself, it only reads
whatever has already been ingested into `storage.documents` / `storage.chunks`
for a given CNPJ. If a company isn't indexed yet, the existing
`scrape_and_ingest()` should be called first (orchestration layer handles
this — see Phase 7).

---

## 2. Data Foundations — What We Can Extract Today

Per the user's instruction: **don't wait for new sources** — build the
Dossier around exactly what's already scraped per company:

| Source | Where it lives | What it contains |
|---|---|---|
| DFP (annual) | `storage.documents["cvm_<cnpj>"]["pages"]` | DRE, BPA, BPP, DFC for `current_year-1` and `current_year-2` (+ any `requested_year` ever queried) |
| ITR (quarterly YTD) | same document, additional pages | Same 4 statement types for `current_year` and `current_year-1` |
| FRE (qualitative) | `storage.documents["fre_<cnpj>_<fre_year>"]["pages"]` | Up to 18 sections (see `CREDIT_SECTIONS` in `fre_client.py` — includes 4 governance sections added 2026-06-12: 1.12, 5.3, 6.5, 7.1), `fre_year = current_year - 1` only (single year, per current pipeline default) |

Each "page" dict currently has: `text`, `page_number`, and (for FRE) `section`,
`section_label`. **The DFP/ITR pages are pre-formatted text** — but the
DataFrame they're built from (`cvm_client.py`) has structured columns
(`CD_CONTA`, `DS_CONTA`, `VL_CONTA`, `ESCALA_MOEDA`, plus a period/date column).
This is the basis for Phase 1.1 below — **we should capture that structure
instead of re-extracting numbers from text via LLM.**

A company may have **multiple years of DFP/FRE accumulated** if it's been
queried multiple times with different `requested_year` values (the store is
append-only). The Dossier builder should pull **everything available for the
CNPJ**, not just the latest year — more data = richer report, per the user's
"extract as many relevant info as possible" directive.

---

## Phase 1 — Company Dossier (Fact Extraction Layer)

> **STATUS: COMPLETE (2026-06-12)**, on branch `feature/company-dossier`
> (off `main`). All of 1.1-1.8 implemented:
> - `app/scraper/cvm_client.py` — `_clean_statement_df()` /
>   `_extract_line_items()` add structured `line_items` to every DFP/ITR page
>   (additive, `text` unchanged).
> - `app/scraper/cvm_registry.py` — `SETOR_ATIV` exposed as `"sector"`
>   (53 distinct values observed); new `get_company_by_cnpj()` for dossier
>   lookups by CNPJ.
> - `app/analysis/schemas.py` — all Pydantic models from 1.2, as specified.
> - `app/analysis/extraction.py` — LLM extraction (Mistral, JSON mode) of
>   `QualitativeFact`s for all 18 FRE sections and `DisclosedMetric`s from 2.5.
> - `app/analysis/dossier_builder.py` — `build_dossier(cnpj)`, persists to
>   `data/dossiers/<cnpj_digits>.json`. Includes conflict detection (1.6,
>   >1% relative diff) and `DossierCoverage` (1.7).
> - `app/generation/llm.py` — added 429 retry/backoff (Mistral rate-limits
>   hard under the ~14-18 concurrent extraction calls per company);
>   `dossier_builder` also caps concurrency at 3 (`_MAX_CONCURRENT_EXTRACTIONS`).
> - `test_dossier.py` — validated end-to-end on Vale and Petrobras (full
>   DFP+ITR+FRE coverage already in storage): ~1,700 financial line items,
>   ~150-185 qualitative facts, 3-8 disclosed metrics each, 0 conflicts,
>   14/18 FRE sections present (the 4 new governance sections require a
>   re-scrape to populate — these companies were scraped before that change).
> - **1.1 migration decision**: went with option (a) — no migration script.
>   `_build_financial_line_items()` reads `page.get("line_items", [])`, so
>   companies scraped before this change simply yield zero line items until
>   re-scraped (staleness-triggered). Acceptable since Phase 8 benchmark
>   companies aren't picked yet and can be (re-)scraped fresh.
>
> **Next**: Phase 3 (Sector Playbooks) — doesn't need the 1-pager template
> either. Phase 4 (section generators) is the first phase that needs it.

**Goal**: one deduplicated, structured, fully-cited JSON document per company
that every downstream stage reads from. No section generator should ever
touch raw `storage.chunks` directly — this is what prevents the
"same number computed three different ways in three sections" problem.

### 1.1 Capture structured financial line items at the source (no LLM)
- [ ] In `app/scraper/cvm_client.py`, locate the loop that currently builds
      formatted text from `CD_CONTA` / `DS_CONTA` / `VL_CONTA` /
      `ESCALA_MOEDA` rows (around the statement-formatting function).
- [ ] Alongside the formatted `text`, also build a `line_items` list:
      ```python
      {
        "account_code": "3.01",                 # CD_CONTA
        "description": "Receita de Venda...",   # DS_CONTA
        "value": 45200000000.0,                 # VL_CONTA, scale-applied
        "scale": "MIL",                         # ESCALA_MOEDA (raw)
        "period_end": "2024-12-31",             # DT_FIM_EXERC or equivalent
        "period_label": "FY 2024",              # existing _period_label()
        "statement_type": "DRE_con",            # which CSV this came from
      }
      ```
- [ ] Attach `line_items` to each page dict returned by `fetch_statements()`
      (additive — existing `text` field stays untouched, so chunking/RAG
      search behavior is unaffected).
- [ ] **Note**: companies scraped *before* this change won't have
      `line_items` in their persisted pages. Decide: (a) only build Dossiers
      for companies re-scraped after this change, or (b) write a small
      migration script that re-derives `line_items` for already-cached raw
      ZIPs (they're still on disk under `data/dfp/` / `data/itr/`). Prefer
      (b) if the target companies for Phase 8 were scraped earlier.

### 1.2 Define the Dossier schema
- [ ] New file `app/analysis/schemas.py` — Pydantic models:
  ```python
  class Citation(BaseModel):
      document_id: str        # e.g. "fre_<cnpj>_2024" or "cvm_<cnpj>"
      filename: str
      section: str | None = None      # FRE section number, e.g. "4.1"
      section_label: str | None = None
      page_number: int | None = None

  class FinancialLineItem(BaseModel):
      account_code: str
      description: str
      value: float
      scale: str
      period_label: str        # "FY 2024", "ITR 2Q2025", etc.
      statement_type: str       # DRE_con, BPA_con, BPP_con, DFC_*_con
      citation: Citation

  class DisclosedMetric(BaseModel):
      """Company-reported non-GAAP figure, e.g. 'Adjusted EBITDA'."""
      label: str                 # company's own term, verbatim
      value: float | None
      unit: str | None           # "R$ milhões", "%", etc.
      period_label: str
      definition: str | None     # how the company defines it (if stated)
      citation: Citation

  class QualitativeFact(BaseModel):
      """One discrete fact/claim extracted from an FRE section."""
      section: str                # "4.1", "1.3", etc.
      section_label: str
      text: str                   # the extracted statement, close to verbatim
      citation: Citation

  class CompanyDossier(BaseModel):
      cnpj: str
      cd_cvm: str
      name: str
      trade_name: str
      sector: str | None = None              # from CVM registry, see 1.5
      generated_at: datetime
      financial_line_items: list[FinancialLineItem]
      disclosed_metrics: list[DisclosedMetric]
      qualitative_facts: list[QualitativeFact]
      conflicts: list["FactConflict"]         # see 1.6
      coverage: "DossierCoverage"             # what's present/missing, see 1.7

  class FactConflict(BaseModel):
      description: str           # e.g. "Net Revenue FY2023 differs between DFP and FRE 2.1"
      values: list[tuple[float, Citation]]

  class DossierCoverage(BaseModel):
      dfp_years: list[int]
      itr_years: list[int]
      fre_years: list[int]
      fre_sections_present: list[str]   # e.g. ["1.2","1.3",...]
      fre_sections_missing: list[str]
  ```
- [ ] Keep this schema additive-friendly — Phase 2 appends `computed_metrics`,
      Phase 4 reads from all of the above without modifying it.

### 1.3 Build the Dossier builder
- [ ] New file `app/analysis/dossier_builder.py`:
  - `build_dossier(cnpj: str) -> CompanyDossier`
  - Reads `storage.documents[f"cvm_{cnpj}"]` for all DFP/ITR pages →
    flattens `line_items` into `financial_line_items`.
  - Reads `storage.documents[f"fre_{cnpj}_{year}"]` for each FRE year present
    → routes each section's text to the appropriate extraction step (1.4).
  - Persist result to `data/dossiers/<cnpj>.json` (cache — rebuild on demand,
    e.g. when new data is scraped for that CNPJ).

### 1.4 LLM extraction — qualitative facts & disclosed metrics
This is the **only** place in Phase 1 where an LLM is used for extraction
(everything financial/numeric in 1.1 is code-based).

- [ ] For each FRE section's text (the 14 `CREDIT_SECTIONS`), prompt Mistral
      with structured-output instructions to return a list of
      `QualitativeFact` candidates: discrete, citable claims (not a summary —
      summarization happens later in Phase 4). Keep extracted text close to
      verbatim so citations remain checkable against the source page.
- [ ] For FRE section **2.5 (Medições não contábeis)** specifically, use a
      dedicated prompt to extract `DisclosedMetric` entries — this is where
      "Adjusted EBITDA", "Recurring EBITDA", company-defined KPIs, and their
      **stated definitions** live. Preserve the company's own labels and
      definitions verbatim — do not normalize to "EBITDA".
- [ ] Each extraction call should return JSON (use Mistral's JSON mode /
      function-calling if available) with a `citation` referencing the
      source page's `page_number` + `section`.

### 1.5 Sector classification
- [ ] CVM's full registry CSV (`cad_cia_aberta.csv`) includes a sector
      classification column (commonly `SETOR_ATIV`) that
      `app/scraper/cvm_registry.py` currently does **not** keep (`_KEEP_COLS`
      only has `CNPJ_CIA, DENOM_SOCIAL, DENOM_COMERC, SIT, CD_CVM`).
- [ ] **Verify the exact column name** in the live CSV (re-download and
      inspect headers — CVM column names occasionally drift).
- [ ] Add it to `_KEEP_COLS` and to `_row_to_dict()`'s return value as
      `"sector"`. Force a registry refresh (`force_refresh=True`) once to
      pick it up for already-cached registries.
- [ ] This `sector` value flows into `CompanyDossier.sector` and is the key
      used to select a Sector Playbook in Phase 3. If CVM's sector taxonomy
      turns out too coarse/fine for our playbooks, we may need a small manual
      `sector_overrides.json` mapping CNPJ → our-own-sector-label — flagged
      as an open question.

### 1.6 Cross-source validation (lightweight, per user's "low priority")
- [ ] When building `financial_line_items`, if the **same account_code +
      period_label** appears with materially different values from two
      sources (e.g., DFP vs. a restated ITR), **don't pick one** — record a
      `FactConflict` entry with both values + citations.
- [ ] Default criterion (placeholder, refine later): flag if relative
      difference > 1% (accounts for rounding) — see
      [Open Questions](#open-questions--decisions-needed).
- [ ] Section generators (Phase 4) and the Composer (Phase 5) should
      "mention both" when a `FactConflict` touches a number they're about to
      use — exact phrasing TBD, low priority per user.

### 1.7 Coverage tracking
- [ ] Populate `DossierCoverage` — which DFP/ITR years are present, which of
      the 14 FRE sections were found vs. missing (the FRE client already logs
      this — see `fre_client.py`'s partial-section logging — just surface it
      into the Dossier instead of only the logs).
- [ ] This feeds directly into Phase 6's "explicit limitations" output.

### 1.8 Test script
- [ ] `test_dossier.py` — build dossiers for 2–3 of the 25 companies known to
      have full FRE+DFP+ITR coverage (e.g. Vale, Ambev, Itaú). Manually
      review the JSON for: correct line items, sensible disclosed metrics,
      reasonable qualitative fact extraction, citations that actually point
      to the right page/section.

---

## Phase 2 — Calculation Engine (standardized metrics, scoped & discardable)

> **DEFERRED — out of scope for v1.** See "Status & Recent Decisions" at the
> top of this file. Do not implement this phase yet; kept for reference only.
> Agents work from disclosed figures (DFP/ITR line items + FRE 2.5 disclosed
> metrics) until this is revisited.

**Framing per discussion**: this is explicitly an experiment. If by end of
week it's not adding value (e.g., the standardized numbers are confusing next
to disclosed numbers, or the account-code mapping is too fragile across
companies), **cut it** and rely on disclosed figures only. Keep this module
isolated (`app/analysis/metrics.py`) so it's easy to remove cleanly.

### 2.1 Account-code mapping — known complexity
CVM's "plano de contas" is standardized at the **top 1–2 levels** (e.g. `3.01`
= Net Revenue, `3.02` = COGS, `1.01` = Current Assets, `2.03` = Equity), but
**diverges below that** — e.g., "Empréstimos e Financiamentos" (debt) can sit
under different sub-codes (`2.01.04`, `2.01.05`, `2.02.01`, ...) depending on
how each company structures its own chart of accounts.

- [ ] Build `ACCOUNT_CODE_MAP` in `app/analysis/metrics.py` for the
      **top-level codes only** (revenue, COGS, gross profit, EBIT-ish line,
      financial result, net income, total assets, current assets, cash,
      current liabilities, non-current liabilities, equity) — these are
      reliable by code across all companies.
- [ ] For **debt specifically** (needed for leverage ratios), match by
      **description pattern** on `DS_CONTA` (regex for "Empréstimos e
      Financiamentos", "Debêntures", "Arrendamento" / "Lease") rather than a
      fixed code — and validate this regex against the BPP `line_items` of
      3–5 real companies before trusting it.
- [ ] D&A for the standardized EBITDA calc: typically easiest to source from
      the DFC (cash flow statement) add-back line ("Depreciação e
      Amortização"), again matched by description pattern within the DFC
      `line_items`.

### 2.2 Canonical metric list (proposed — confirm/adjust with user)
| Category | Metric | Formula (from line items) |
|---|---|---|
| Profitability | Standardized EBITDA | EBIT-ish line + D&A (from DFC) |
| Profitability | Standardized EBITDA Margin | Standardized EBITDA / Net Revenue |
| Profitability | Gross Margin | Gross Profit / Net Revenue |
| Profitability | Net Margin | Net Income / Net Revenue |
| Leverage | Total Debt | Σ debt-pattern line items (current + non-current) |
| Leverage | Net Debt | Total Debt − Cash & Equivalents |
| Leverage | Net Debt / Standardized EBITDA | — |
| Coverage | Interest Coverage Ratio | Standardized EBITDA / Financial Expenses |
| Liquidity | Current Ratio | Current Assets / Current Liabilities |
| Cash Flow | FCF (proxy) | CFO − Capex (from DFC) |
| Growth | Revenue YoY % | (Rev_t − Rev_{t-1}) / Rev_{t-1} |
| Growth | Standardized EBITDA YoY % | same pattern |

### 2.3 Implementation
- [ ] Pure functions in `app/analysis/metrics.py`, e.g.
      `compute_metrics(dossier: CompanyDossier) -> list[ComputedMetric]`,
      one `ComputedMetric` per (metric, period) pair. No LLM calls in this
      module — period.
- [ ] Append `computed_metrics: list[ComputedMetric]` to the Dossier (or keep
      as a sibling artifact — your call when implementing; sibling is easier
      to discard cleanly per the framing above).

### 2.4 Divergence flagging (vs. disclosed)
- [ ] Where a `DisclosedMetric` and a `ComputedMetric` represent "the same
      concept" (e.g., disclosed "Adjusted EBITDA" vs. standardized EBITDA),
      **don't reconcile** — compute the % difference and surface it as a
      one-line note for the relevant section ("Adjusted EBITDA disclosed by
      the company is X% higher than the standardized figure, primarily
      reflecting [unspecified add-backs]"). This is analytically useful, not
      an error.

### 2.5 Test script
- [ ] `test_metrics.py` — run on the same 2–3 companies as `test_dossier.py`,
      sanity-check ratios against publicly known figures (e.g., does Vale's
      computed Net Debt/EBITDA look like a plausible number?).

---

## Phase 3 — Sector Playbooks (MIT Faculty Lens)

> **STATUS: INFRASTRUCTURE COMPLETE (2026-06-12)**, on branch
> `feature/sector-playbooks` (off `main`). 3.1, 3.3, 3.4 done:
> - `data/playbooks/_template.md` — the 5-section template (3.1), with usage
>   notes for filling it in during faculty meetings.
> - `data/playbooks/_default.md` — a sector-agnostic credit-analysis
>   framework (same 5-section structure), used as the fallback for every
>   sector until a real playbook exists for it.
> - `app/analysis/playbooks.py` — `load_playbook(sector) -> str` (3.3/3.4).
>   `_slugify(sector)` maps CVM's `SETOR_ATIV` value to a filename slug and
>   strips the "Emp. Adm. Part. - X" holding-company prefix so a holding
>   company slugs to the same playbook as sector X.
> - `test_playbooks.py` — validated `_slugify()` against all 70 distinct
>   `SETOR_ATIV` values from `data/cvm_registry.csv`; confirmed
>   holding-company variants map to their base sector's slug (with one known
>   exception, see Open Question #3); confirmed `load_playbook()` falls back
>   to `_default.md` for every sector (no sector-specific files exist yet).
>
> **Not done**: 3.2 (faculty elicitation) — no sector-specific playbook
> *content* exists yet. This doesn't block anything: every company currently
> gets `_default.md`. Once a faculty meeting happens, drop
> `data/playbooks/<sector_slug>.md` in and it's picked up automatically, with
> zero code changes.
>
> **Next**: Phase 4 (section generators) — first phase needing the 1-pager
> template, but can start with the v1 agent roster (decision 5) as a
> placeholder.

This is the differentiator — treat it as **in-context retrieval of expert
frameworks**, not fine-tuning (not realistic for this timeline).

### 3.1 Playbook template
- [x] Create `data/playbooks/_template.md` with a fixed structure mirroring
      the elicitation questions:
  ```markdown
  # Sector Playbook: <Sector Name>

  ## Source
  - Faculty/practitioner: <name, role>
  - Date: <date>

  ## 1. Top credit risk drivers for this sector
  (2-3 things that matter most, that generic financial analysis underweights)

  ## 2. Common analyst mistakes / oversimplifications
  -

  ## 3. Signals: "good" vs "concerning"
  | Signal | Looks healthy when... | Red flag when... |
  |---|---|---|

  ## 4. Sector-specific metrics or framings
  (e.g., same-store sales for retail, reserve replacement for E&P)

  ## 5. Characteristic reasoning / language
  (How does this expert frame a credit opinion? Include a worked example
   if possible — even informal, "Professor X said about Company Y: ...")
  ```

### 3.2 Elicitation guide for faculty meetings (this week)
- [ ] Use the 5 questions above as a loose interview guide during faculty
      meetings — doesn't need to be read verbatim, but try to come away with
      something for each of the 5 sections per sector discussed.
- [ ] Capture notes in whatever form is fastest during the meeting (voice
      memo → transcript → fill template afterward is fine) — the template is
      for the *output*, not necessarily the live note-taking format.
- [ ] **Each playbook is independently useful the moment it exists** — no
      need to wait for all sectors before wiring Phase 3.3/3.4.

**Scalability / how many sectors, really?** CVM's registry has 70 distinct
`SETOR_ATIV` values, but ~30 of those are "Emp. Adm. Part. - X"
holding-company variants that automatically share sector X's playbook (via
`_slugify()`'s prefix-stripping — see Phase 3 status box). So there are **38
substantive sectors** that would each benefit from their own playbook — that
is the real number to plan faculty meetings around, not 70.

Adding a sector later is zero-code-change: drop `data/playbooks/<slug>.md`
in and `load_playbook()` picks it up automatically. The mechanism scales to
all 38 (or more); the bottleneck is purely the number of faculty meetings.
**Recommended prioritization**: cover the sectors of the Phase 8 benchmark
companies first (that list already spans mining, oil & gas, banking, retail,
healthcare, etc.) — `_default.md`'s generic framework covers every other
sector indefinitely, so there's no "broken" state for uncovered sectors,
just a less sector-specific judgment section.

### 3.3 Storage + loader
- [x] `data/playbooks/<sector_slug>.md` — one file per sector (none created
      yet — pending 3.2; mechanism is ready).
- [x] `data/playbooks/_default.md` — generic credit-analysis framework, used
      as fallback when no sector-specific playbook exists yet (so the
      pipeline never breaks for an uncovered sector — it just gets a more
      generic "judgment" section).
- [x] `app/analysis/playbooks.py`:
  - `load_playbook(sector: str | None) -> str` — returns playbook markdown
    text (or `_default.md` if `sector` is `None` or no matching file).
  - Sector → filename mapping: `_slugify()` (lowercase, hyphens, accents
    stripped, "Emp. Adm. Part. -" holding prefix stripped).

### 3.4 Sector → playbook wiring
- [x] `CompanyDossier.sector` (from Phase 1.5) is the lookup key.
- [x] CVM's sector taxonomy (70 distinct `SETOR_ATIV` values) is fine-grained
      enough to use directly — no "Retail"-style remapping needed for the 38
      substantive sectors. The ~30 "Emp. Adm. Part. - X" holding-company
      variants are handled by `_slugify()` stripping that prefix. One known
      mismatch (CVM spells the construction-sector holding variant
      differently from the base sector — see Open Question #3) falls back to
      `_default.md` rather than sharing a future construction playbook;
      low-impact, revisit if a Phase 8 benchmark company is affected.

---

## Phase 4 — Fact/Inference/Judgment Tagging + Section Generators

### 4.1 Tagging schema
Internal representation — not necessarily what's *displayed* (display format
TBD pending templates), but how section generators structure their output so
Phase 6 can check it.

```python
class TaggedStatement(BaseModel):
    type: Literal["fact", "inference", "judgment"]
    text: str
    citations: list[Citation] = []          # required for "fact"
    derived_from: list[str] | None = None    # for "inference": refs to other
                                              # statement texts/ids it combines
    basis: str | None = None                 # for "judgment": which playbook
                                              # section informed this

class SectionOutput(BaseModel):
    section_id: str           # e.g. "business_overview", "credit_metrics"
    title: str
    statements: list[TaggedStatement]
```

- **Fact**: directly stated in a source doc — `citations` non-empty,
  `derived_from`/`basis` null.
- **Inference**: derived via logic/arithmetic from facts — `derived_from`
  references the facts combined; citations may be empty if purely
  computational (the computation itself is traceable via `derived_from` →
  those facts' citations).
- **Judgment**: requires the sector playbook — `basis` references which part
  of the playbook informed it (e.g., "Sector Playbook §1: liquidity drivers").

### 4.2 v1 agent roster (supersedes earlier placeholder — see Status & Recent Decisions)
Each agent receives the **full** `CompanyDossier` (Decision 1) — the "Focus /
primary sources" column below describes what each agent is *prompted to
emphasize*, not what it's restricted to. All agents also receive
`dossier.sector` + the matching sector playbook (Phase 3); sector-specific
behavior comes from the prompt, not from a different roster (Decision 4).
**Expect to revise once 1-pager/memo templates arrive** — the
section-generator pattern doesn't change, only this list might.

| # | Agent / Section | Type | Focus / primary sources |
|---|---|---|---|
| 1 | Business & Segments | fact-heavy | Identity, FRE 1.2/1.3/1.6/2.10 |
| 2 | Financial Performance | fact + inference | DRE line items, FRE 2.1/2.2 |
| 3 | Debt & Capital Structure | fact + inference | BPP/BPA line items, FRE 2.1, FRE 1.15 (contracts/covenants) |
| 4 | Cash Flow & Liquidity | fact + inference | DFC line items |
| 5 | Risk Factors & Contingencies | fact + judgment | FRE 4.1/4.2/4.3/4.7/5.1/2.8 + sector playbook |
| 6 | Non-GAAP / KPIs | fact + inference | FRE 2.5 disclosed metrics |
| 7 | **Governance & Ownership Structure (new)** | fact-heavy | FRE 1.12/5.3/6.5/7.1 |
| 8 | MIT Outlook (judgment) | judgment (heaviest) | sector playbook §5 + outputs of agents 1-7 |
| 9 | Limitations & Coverage (meta) | meta | DossierCoverage + conflicts + outputs of agents 1-8 |

Execution order matters (Decision 6): agents 1-7 run first (any order/
parallel), then agent 8 (Outlook) receives their `SectionOutput`s as extra
context, then agent 9 (Limitations) runs last.

### 4.3 Section generator implementation
- [ ] `app/analysis/sections.py` (or `app/analysis/sections/<id>.py` if it
      gets large) — one function per agent from the 4.2 roster:
      `generate_<section_id>(dossier, playbook, prior_sections=None) -> SectionOutput`
      (no `computed_metrics` param — Phase 2 deferred, see Status & Recent
      Decisions).
- [ ] Each function receives the **full** `CompanyDossier` (Decision 1) and
      builds a prompt with: the full Dossier (or as much as fits — measure
      this empirically per Phase 1.8), the sector playbook text, and — for
      agents 8/9 — `prior_sections` (the `SectionOutput`s already generated).
      The prompt tells the agent which lens/specialization it is (e.g. "you
      are the Debt & Capital Structure agent — extract everything relevant to
      debt, leverage, and covenants, even if mentioned in a risk-factors or
      contracts section").
- [ ] **Fallback search (Decision 2)**: if the Dossier looks thin for this
      agent's topic (e.g. relevant FRE section in
      `DossierCoverage.fre_sections_missing`), call
      `vector_store.search()` / `bm25_index.search()` directly for a few
      extra chunks before building the prompt — plain function call, not a
      tool-use loop. Any fact sourced this way still gets a `Citation`.
- [ ] Prompt instructs the LLM to return `SectionOutput`-shaped JSON
      (fact/inference/judgment tagged statements with citations referencing
      `Citation` objects already present in the Dossier — i.e., the LLM
      should **select** citations from what it's given, not invent new ones).

### 4.4 Output format
- [ ] Keep `SectionOutput` as the canonical generator output (structured),
      separate from the rendered Markdown (Phase 5). This lets Phase 6
      inspect tags/citations programmatically before anything is rendered to
      prose.

---

## Phase 5 — Composer (1-Pager + Full Memo)

- [ ] `app/analysis/composer.py`:
  - `compose_one_pager(dossier, sections: list[SectionOutput]) -> str` (Markdown)
  - `compose_memo(dossier, sections: list[SectionOutput]) -> str` (Markdown)
- [ ] Both composers read from the **same** `sections` list — guarantees the
      1-pager and memo never disagree (1-pager is a condensed
      view/selection, not an independently-generated summary).
- [ ] Citation rendering: reuse the existing numbered-citation convention
      from `app/generation/prompts.py` (`build_rag_prompt`'s `[1]`, `[2]`
      style) — render each `TaggedStatement`'s citations as `[n]` inline,
      with a references appendix mapping `[n]` → document/section/page.
- [ ] Output as Markdown for now (matches "first version" scope). PDF/Word
      export is a later-week concern (see
      [Looking Ahead](#looking-ahead-how-this-maps-to-weeks-47)) — don't
      build rendering infrastructure for that yet.
- [ ] **Pending templates**: once you share the 1-page/memo templates, this
      is the file that changes most — the section list (4.2) and the
      composition logic here adapt to match the template's structure and
      ordering.

---

## Phase 6 — Review / Critic Agent + Error Log

### 6.1 Citation coverage check
- [ ] For every `TaggedStatement` of type `"fact"`, verify `citations` is
      non-empty and each `Citation` resolves to a real
      `document_id`/`section`/`page` in the Dossier. Flag violations as
      `critical` errors — per Week 6's eventual guardrail ("no financial
      metric without source"), but for this week, **log rather than block**.

### 6.2 Internal consistency check
- [ ] Cross-reference numeric values mentioned across different
      `SectionOutput`s against `financial_line_items` /
      `computed_metrics` — flag if a section states a number that doesn't
      match the Dossier's value for that (account, period).
- [ ] Cross-reference against `Dossier.conflicts` (Phase 1.6) — if a section
      uses a figure that has a recorded conflict, verify the "mention both"
      convention was applied.

### 6.3 Confidence score (v0 — explicitly a first pass)
- [ ] Simple weighted heuristic, e.g.:
  - `% of FRE sections present / 14` (coverage)
  - `% of "fact" statements with valid citations` (traceability)
  - `1 − (# unresolved conflicts / # numeric facts used)` (consistency)
  - `1` if no disclosed-vs-computed divergence > threshold, else scaled down
  - Combine into a single 0–1 score + a breakdown dict so it's interpretable,
    not a black box.
- [ ] Explicitly label this as v0 in the output — refine after seeing it run
      against real companies and (eventually) against analyst feedback in
      later weeks.

### 6.4 Error log + limitations output
- [ ] `app/analysis/schemas.py` additions:
  ```python
  class ErrorLogEntry(BaseModel):
      severity: Literal["critical", "warning", "info"]
      stage: Literal["extraction", "calculation", "validation", "generation", "review"]
      message: str
      location: str | None = None   # e.g. "section=credit_metrics, statement_idx=3"

  class AnalysisRun(BaseModel):
      cnpj: str
      company_name: str
      generated_at: datetime
      one_pager_md: str
      memo_md: str
      sections: list[SectionOutput]
      limitations: list[str]          # human-readable, derived from DossierCoverage
      error_log: list[ErrorLogEntry]
      confidence_score: float
      confidence_breakdown: dict[str, float]
  ```
- [ ] Persist each run to `data/analysis_runs/<cnpj>/<timestamp>/` — keeps
      historical runs for comparison as the pipeline improves week-to-week
      (directly useful for Week 5's "deep review of a sample" and Week 7's
      "compare agent output to human analysis").

---

## Phase 7 — Orchestration & API

### 7.1 Pipeline entrypoint
- [ ] `app/analysis/pipeline.py`:
  ```python
  async def generate_credit_analysis(
      company_query: str,
      year: int | None = None,
      progress: ProgressCallback | None = None,
  ) -> AnalysisRun:
      # 1. Resolve company (reuse cvm_registry.lookup_company)
      # 2. Ensure data is indexed — call scrape_and_ingest() if not
      #    (reuse app/scraper/pipeline.py, same emit() pattern)
      # 3. build_dossier()  [Phase 1]
      # 4. compute_metrics() [Phase 2]
      # 5. load_playbook(dossier.sector) [Phase 3]
      # 6. run all section generators [Phase 4]
      # 7. compose_one_pager() / compose_memo() [Phase 5]
      # 8. run reviewer → error_log, confidence [Phase 6]
      # 9. persist AnalysisRun, return it
  ```
- [ ] Mirror the `emit()` progress-callback pattern from
      `app/scraper/pipeline.py` so this can eventually stream progress over
      SSE the same way `/query/stream` does.

### 7.2 Router
- [ ] New `app/routers/analysis.py`:
  - `POST /analysis/generate` — body: `{"company": "...", "year": 2024}` →
    triggers `generate_credit_analysis`, returns `AnalysisRun` (or an SSE
    stream variant `/analysis/generate/stream`, mirroring `/query/stream`,
    if generation is slow enough to warrant progress updates).
  - `GET /analysis/{cnpj}` — returns the most recent persisted `AnalysisRun`
    for that company, if any.
- [ ] Wire the router into `app/main.py`.

### 7.3 Persistence layout
```
data/
  dossiers/
    <cnpj>.json                      # Phase 1 cache, rebuilt on demand
  playbooks/
    _template.md
    _default.md
    <sector_slug>.md
  analysis_runs/
    <cnpj>/
      <timestamp>/
        one_pager.md
        memo.md
        run.json                     # full AnalysisRun incl. error_log
```
- [ ] Add `data/dossiers/`, `data/analysis_runs/` to `.gitignore` (consistent
      with how `data/persist/` is already excluded) — these are
      regeneratable caches, not source.
- [ ] `data/playbooks/` should **NOT** be gitignored — these are
      hand-curated, valuable artifacts from faculty meetings.

---

## Phase 8 — Benchmark Round Prep

### 8.1 Target companies
- [ ] Pick 3–5 companies from the 25 verified-working FRE companies (14/14
      sections, reference-year 2023+): Petrobras, Vale, Telefônica Brasil,
      Ambev, Itaú Unibanco, Embraer, JBS, Suzano, Magazine Luiza, Localiza,
      Marfrig, Hapvida, Azul, Track & Field, Allied, OceanPact, Ser
      Educacional, Vivara, Smartfit, Intelbras, Vamos, PetroRecôncavo,
      Direcional Engenharia, Cruzeiro do Sul Educacional, Mater Dei — that
      also have solid DFP/ITR coverage — ideally spanning different sectors
      so Phase 3 playbook coverage gaps become visible early (e.g., one from
      a sector you're meeting faculty about this week).
- [ ] Run `generate_credit_analysis()` for each, end-to-end.

### 8.2 Feedback capture (oral feedback → notes)
- [ ] Since benchmark feedback will be oral, prepare a lightweight
      structured note-taking template *before* the sessions — e.g.
      `data/feedback/_template.md`:
  ```markdown
  # Benchmark session — <date>
  Reviewer: <name, role>
  Company/analysis reviewed: <cnpj / name, run timestamp>

  ## Section-by-section notes
  | Section | Comment | Severity (critical/minor/style) |
  |---|---|---|

  ## General impressions
  -

  ## Specific factual errors found
  -

  ## "MIT lens" — did the judgment sections sound like expert reasoning?
  -
  ```
- [ ] This both captures feedback *and* doubles as input for Phase 6's
      confidence score refinement and Week 6's "additional skills" backlog.

### 8.3 Run log
- [ ] Keep a simple running list (could just be a section in this file, or a
      `data/analysis_runs/RUNLOG.md`) of: company, date, confidence score,
      notable error_log entries, anything that needed manual correction —
      this becomes the "documented error log" deliverable at an aggregate
      level, distinct from the per-run `error_log` inside each `AnalysisRun`.

---

## File / Module Map

| Action | File | Responsibility |
|---|---|---|
| Done | `app/scraper/fre_client.py` | Added governance sections 1.12/5.3/6.5/7.1 to `CREDIT_SECTIONS`/`XML_TAG_TO_SECTION` (merged to `main`) |
| Done | `app/scraper/cvm_client.py` | `_clean_statement_df()`/`_extract_line_items()` emit structured `line_items` alongside formatted text (1.1) — on `feature/company-dossier` |
| Done | `app/scraper/cvm_registry.py` | `SETOR_ATIV` exposed as `"sector"` (1.5) + new `get_company_by_cnpj()` — on `feature/company-dossier` |
| Done | `app/analysis/__init__.py` | — |
| Done | `app/analysis/schemas.py` | All Phase 1 Pydantic models: `Citation`, `FinancialLineItem`, `DisclosedMetric`, `QualitativeFact`, `FactConflict`, `DossierCoverage`, `CompanyDossier` |
| Done | `app/analysis/extraction.py` | LLM extraction (Mistral, JSON mode): `QualitativeFact`s from all 18 FRE sections, `DisclosedMetric`s from 2.5 |
| Done | `app/analysis/dossier_builder.py` | `build_dossier(cnpj)` → persists `data/dossiers/<cnpj_digits>.json`; includes conflict detection (1.6) + `DossierCoverage` (1.7) |
| Done | `app/generation/llm.py` | Added 429 retry/backoff to `chat_completion()` (needed for concurrent FRE extraction calls) |
| Done | `.gitignore` | Added `data/dossiers/*` + `.gitkeep` |
| Done | `test_dossier.py` | Validated end-to-end on Vale + Petrobras |
| — | `app/analysis/metrics.py` | DEFERRED (Phase 2 — Calculation Engine, out of scope for v1) |
| Done | `app/analysis/playbooks.py` | `load_playbook(sector)` + `_slugify()` (3.3/3.4) — on `feature/sector-playbooks` |
| Done | `data/playbooks/_template.md`, `_default.md` | Playbook template (3.1) + sector-agnostic fallback playbook (3.3) — on `feature/sector-playbooks` |
| Done | `test_playbooks.py` | Validated `_slugify()` against all 70 `SETOR_ATIV` values + `load_playbook()` fallback |
| Not started | `app/analysis/sections.py` | One generator function per agent (9-agent roster) — Phase 4 |
| Not started | `app/analysis/composer.py` | `compose_one_pager()`, `compose_memo()` — Phase 5 |
| Not started | `app/analysis/reviewer.py` | Citation/consistency checks, confidence score, error log — Phase 6 |
| Not started | `app/analysis/pipeline.py` | `generate_credit_analysis()` orchestrator — Phase 7 |
| Not started | `app/routers/analysis.py` | `/analysis/generate`, `/analysis/{cnpj}` — Phase 7 |
| Not started | `app/main.py` | Register new router — Phase 7 |
| Not started | `data/playbooks/<sector_slug>.md` | Real sector playbooks — Phase 3.2, pending faculty meetings |
| Not started | `data/feedback/_template.md` | Benchmark session note template — Phase 8 |
| Not started | `test_metrics.py`, `test_analysis_pipeline.py` | Standalone test scripts — Phase 2 (deferred) / Phase 7 |

---

## Open Questions / Decisions Needed

These don't block starting Phase 1, but will need answers as we go:

1. **Templates** — waiting on 1-pager and full memo templates from you.
   Section list in 4.2 is a placeholder until then.
2. **Canonical ratio list (Phase 2.2)** — moot for now, Phase 2 is deferred
   (see Status & Recent Decisions). Revisit when computations come back into
   scope.
3. **Sector taxonomy** — RESOLVED for now: CVM's 70 `SETOR_ATIV` values (38
   substantive sectors + ~30 "Emp. Adm. Part. - X" holding variants + 2
   edge cases) are used directly as playbook slugs via `_slugify()`, which
   strips the holding-company prefix so holdings share the underlying
   sector's playbook. **One known mismatch**: CVM spells the construction
   sector differently for the base ("Construção Civil, Mat. Constr. e
   Decoração") vs. holding ("Emp. Adm. Part. - Const. Civil, Mat. Const. e
   Decoração") variant, so they slug to different filenames — a future
   construction playbook would need to be duplicated under both slugs (or we
   add a 1-line manual alias) if a holding company in that sector becomes
   relevant. Low priority — revisit if it affects a Phase 8 company.
4. **Conflict threshold (1.6)** — is >1% relative difference the right
   trigger for "mention both numbers", or should it be looser (e.g. >5%) to
   avoid noise from rounding?
5. **Confidence score formula (6.3)** — v0 is a simple weighted heuristic;
   revisit weights once we see real scores against real analyses.
6. **Fact/inference/judgment display** — confirmed internal-only for now;
   revisit once templates show whether readers should see these tags
   (e.g., visually distinguished "Analyst View" boxes) or whether it's purely
   for the error-log/review pipeline.
7. **Market valuation / macro data in agents** (see architecture decision 7)
   — `fetch_market_data()` (Yahoo/yfinance) and `get_macro_data()` (BCB SGS +
   Focus) are live external feeds, deliberately NOT part of `CompanyDossier`.
   Which Phase 4 agent(s) should call them, and how should that show up in
   the report (dedicated subsection vs. inline framing within e.g. MIT
   Outlook or Debt & Capital Structure)? Not decided.

---

## Looking Ahead: How This Maps to Weeks 4–7

Architecture choices this week are made with these in mind — not over-built
for them now, but structured so they're additive later:

- **Week 4 (customization, sector/company-specific skills, focus options)**
  → Builds directly on Phase 4's per-section-generator pattern (add new
  section types / make existing ones configurable) and Phase 3's playbook
  system (more playbooks = more sector specificity). "Focus options" (e.g.,
  "emphasize liquidity") becomes a parameter that selects which sections run
  or expands a section's prompt.
- **Week 4 (editable outputs, feedback mechanism)**
  → Phase 5's Markdown output is naturally editable. Phase 8.2's feedback
  template is the seed of the "simple feedback mechanism."
- **Week 5 (scale + dashboard)**
  → Phase 7's persisted `AnalysisRun` objects (with `confidence_score`,
  `error_log`, timing if we add it) are exactly the records a dashboard would
  aggregate across companies. Phase 1.3's Dossier caching means re-running at
  scale doesn't re-extract from scratch.
- **Week 6 (guardrails, quality score, additional skills)**
  → Phase 6's checks become **blocking** instead of log-only ("no conclusion
  without evidence" = citation coverage check becomes a hard gate).
  `confidence_score` becomes "quality score." New skills (liquidity, debt
  maturity, red flags, peer comparison, committee summary, analyst review)
  are new entries in Phase 4's section-generator set, all reading from the
  same Dossier shape.
- **Week 7 (pilot, compare to human analysis)**
  → Requires the Composer output to be analyst-friendly/exportable
  (Markdown → Word/PDF) — not needed now, but Phase 5's clean separation of
  "structured sections" vs. "rendered output" means swapping/adding a
  renderer later doesn't touch the generation logic.
