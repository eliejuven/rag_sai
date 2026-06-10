# Project Context — RAG SAI (Credit Analysis Agent for Itaú)

## What this project is

A Wall Street-style credit analysis agent built to assist Itaú's credit committee. The goal is to help credit analysts produce credit analyses and memos for Brazilian listed companies. When a user asks a question about a company (e.g. "quais são os principais riscos da Petrobras?"), the system automatically fetches the right financial documents from CVM, indexes them, and answers with citations.

This is a RAG (Retrieval-Augmented Generation) pipeline — no LangChain, no ChromaDB, everything built from scratch.

---

## How to run

```bash
# Activate the virtualenv (IMPORTANT — always use .venv, not system python)
source .venv/bin/activate

# Or run directly without activating:
.venv/bin/python3 -m uvicorn app.main:app --reload --port 8001
```

The app runs at `http://localhost:8001`. Swagger UI at `/docs`.

**Critical env note:** `python3` on this machine maps to pyenv 3.12.0 (system), not the project venv. Always use `.venv/bin/python3` when running scripts directly.

```bash
# .env file must contain:
MISTRAL_API_KEY=your_key
```

---

## Tech stack

| Component | Library | Details |
|-----------|---------|---------|
| API server | FastAPI + Uvicorn | ASGI, SSE streaming |
| LLM (chat) | Mistral AI | `mistral-small-latest` |
| Embeddings | Mistral AI | `mistral-embed`, 1024-dim vectors |
| Vector store | NumPy (from scratch) | cosine similarity, dot product on L2-normalized matrix |
| Keyword search | BM25 (from scratch) | catches exact account codes, tickers |
| Reranking | Reciprocal Rank Fusion | `score = Σ 1/(k+rank)`, k=60 |
| PDF parsing | pdfplumber | word-level extraction, groups by `top` coordinate |
| CVM data | requests | dados.cvm.gov.br + rad.cvm.gov.br |
| Persistence | JSON + .npy files | `data/persist/` |

---

## Project file structure

```
app/
  main.py                    # FastAPI app, mounts routers
  config.py                  # All tunable parameters (chunk size, top-k, etc.)
  storage.py                 # Module-level globals: documents (dict), chunks (list)
  models.py                  # Pydantic models: QueryRequest, QueryResponse, ChunkResult
  persistence.py             # save_state() / load_state() for chunks+vectors+bm25
  routers/
    query.py                 # POST /query/stream (SSE) and POST /query
  scraper/
    cvm_registry.py          # Downloads cad_cia_aberta.csv, resolves name → CNPJ
    cvm_client.py            # Downloads DFP/ITR ZIPs, reads CSVs, formats statements
    fre_client.py            # Downloads FRE ZIPs, extracts 14 credit-relevant sections
    pipeline.py              # Orchestrates: registry → DFP/ITR + FRE → chunk → embed → store
  search/
    vector_store.py          # NumPy cosine similarity search
    keyword_search.py        # Pure-Python BM25
    reranker.py              # Reciprocal Rank Fusion
  query/
    intent.py                # LLM classifies "search" vs "chat"
    company_extractor.py     # LLM extracts company name + regex extracts year
    transform.py             # Query rewriting for better semantic search
  generation/
    llm.py                   # Mistral chat_completion()
    prompts.py               # build_system_prompt(), build_rag_prompt()
    skills.py                # Detects financial/tabular data, appends number-parsing instructions
  embeddings/
    client.py                # embed_texts() — batches, calls Mistral embed API
  ingestion/
    chunker.py               # chunk_pages() — size 2000, overlap 400
    pdf_parser.py            # For user-uploaded PDFs (not CVM scraper)
  static/
    index.html               # Single-file chat UI with SSE, citations, source panel
data/
  fre/                       # FRE metadata ZIPs (one per year)
    .gitkeep
    docs/                    # Per-company FRE document ZIPs
      .gitkeep
  dfp/                       # DFP annual ZIPs (cached, gitignored)
  itr/                       # ITR quarterly ZIPs (cached, gitignored)
  persist/                   # chunks.json, vectors.npy, vector_meta.json, bm25.json (gitignored)
```

---

## Full request flow (POST /query/stream)

1. **Intent detection** (`app/query/intent.py`) — LLM decides "search" or "chat". Chat questions skip retrieval entirely.

2. **Company + year extraction** (`app/query/company_extractor.py`)
   - LLM extracts ANY company name (even unknown ones like "Axia Energia")
   - Year extracted by regex `\b(20\d{2})\b`
   - If official name differs from what user typed (e.g. Vivo → Telefônica Brasil), an `alias_hint` string is built and injected into the RAG prompt

3. **First search attempt** — vector + BM25 hybrid on existing in-memory store. Results are discarded if they belong to a different company or don't cover the requested year.

4. **Auto-scrape** (`app/scraper/pipeline.py`) — if search fails, `scrape_and_ingest()` runs:
   - Resolves company in CVM registry (3-tier fuzzy matching)
   - Downloads DFP (annual) + ITR (quarterly) ZIPs from CVM
   - Downloads FRE ZIP from RAD CVM, extracts 14 qualitative sections
   - Chunks + embeds + stores everything
   - Each step emits progress via async callback → SSE queue → browser

5. **Generation** (`app/generation/`) — reranked chunks injected into RAG prompt. If tabular/financial data detected, number-parsing instructions are appended.

---

## CVM scraper pipeline (scrape_and_ingest)

Located in `app/scraper/pipeline.py`.

```
Step 1 — CVM registry lookup
  cvm_registry.py downloads cad_cia_aberta.csv
  3-tier matching: exact trade name → exact legal name → partial token match
  Token filter: len(t) > 1 (to preserve "do", "da", "de" particles)
  Returns: { cnpj, name, trade_name, cd_cvm }

Step 2 — Staleness check
  Skips if: company scraped < 90 days ago AND requested year is in dfp_years AND fre_year is in fre_years

Step 3a — DFP + ITR fetch (cvm_client.py)
  Downloads ZIP files (~13MB each, covers all ~500 B3 companies)
  Reads target CSVs directly from inside the ZIP (no disk extraction)
  Applies scale multipliers, formats 4 statement types:
    DRE (income statement), BPA (assets), BPP (liabilities), DFC (cash flow)
  Filters for "ÚLTIMO" (most recent period per year)

Step 3b — FRE fetch (fre_client.py) ← NEW (branch: formulario_de_referencia_retrieval)
  Non-fatal: if FRE fails, pipeline continues with just DFP/ITR
  See FRE section below for details

Step 4 — Chunk
  DFP/ITR: merged chunking (continuous dataset)
  FRE: section-by-section to preserve section metadata in each chunk

Step 5 — Embed + store
  Embeds all chunks via Mistral embed API
  Adds to NumPy vector store + BM25 index
  Registers DFP/ITR document and FRE document separately in storage

Step 6 — Persist metadata
  Saves dfp_years, itr_years, fre_years, last_scraped, chunk counts
  Persists vectors/chunks/bm25 to data/persist/
```

---

## FRE client (app/scraper/fre_client.py) — THE KEY NEW FILE

The Formulário de Referência is a 600-page qualitative annual disclosure that every B3 company files on CVM. It contains what structured financial data doesn't: risk factors, management commentary, EBITDA definitions, business plans.

### How the FRE is stored on CVM

- **Metadata index**: `https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FRE/DADOS/fre_cia_aberta_{year}.zip`
  - One CSV row per filing version. We take the highest `VERSAO` number (latest revision).
  - Returns a `LINK_DOC` URL pointing to the document ZIP.
- **Document ZIP**: `https://www.rad.cvm.gov.br/ENETCONSULTA/frmDownloadDocumento.aspx?...`
  - The RAD server blocks httpx (connection reset). Must use `requests.Session` with browser headers.
  - URLs in the CSV use `http://` — must be forced to `https://` before requesting.
  - The ZIP contains one large XML file (~tens of MB).
  - The XML embeds each section as a base64-encoded PDF inside `<ImagemObjetoArquivoPdf>` tags.

### Critical design decision — XML tag-based extraction

**DO NOT** use filenames to identify sections. Filenames (`<NomeArquivoPdf>`) are free-text typed by each company's IR team and are inconsistent:

| Company | Filename for section 4.1 | Problem |
|---------|--------------------------|---------|
| Petrobras | `4.1_Rod2 Validação_RI.docx` | Works |
| Vale | `FRE 2025 (FY 2024) item 4.1.pdf` | Number buried in text |
| Telefônica | `4.01.pdf` | Zero-padded |
| JBS | `4.1 - JBS S.A. - FRE 2025...` | Space before number |
| JBS | `5,5,1 - JBS...` for section 5.1 | Commas instead of dots |

**The correct approach**: Use the CVM-mandated XML schema tags. The `<DescricaoFatoresRisco>` tag ALWAYS wraps section 4.1, regardless of the filename inside it.

```python
XML_TAG_TO_SECTION = {
    "AtividadesEmissorControladas":     "1.2",
    "InfoSegmentosOperacionais":        "1.3",
    "EfeitosRegulacaoEstatal":          "1.6",
    "ContratosRelevantes":              "1.15",
    "CondicoesFinanceirasPatrimoniais": "2.1",
    "ResultadosOperFinanceiros":        "2.2",
    "MedicoesNaoContabeis":             "2.5",
    "ItensRelevantesNaoEvidenciadosDF": "2.8",
    "PlanoNegocios":                    "2.10",
    "DescricaoFatoresRisco":            "4.1",
    "Descricao5PrincipaisFatoresRisco": "4.2",
    "DescricaoRiscosMercado":           "4.3",
    "ContingenciasRelevantes":          "4.7",
    "DescricaoGerenciamentoRiscos":     "5.1",
}
```

### Credit-relevant sections extracted (14 total)

| Section | Content | Why relevant |
|---------|---------|--------------|
| 1.2 | Business activities | Business description |
| 1.3 | Operating segments | Revenue breakdown |
| 1.6 | State regulation | Regulatory risk |
| 1.15 | Relevant contracts | Debt covenants, supply agreements |
| 2.1 | Financial conditions | MD&A — liquidity, capital structure |
| 2.2 | Operating/financial results | MD&A — revenue, EBITDA discussion |
| 2.5 | Non-accounting measures | Management-defined EBITDA, KPIs |
| 2.8 | Off-balance sheet items | Hidden liabilities |
| 2.10 | Business plans | Capex, growth strategy |
| 4.1 | Risk factors | Full risk disclosure |
| 4.2 | Top 5 risk factors | Most critical risks |
| 4.3 | Market risks | FX, commodity, interest rate |
| 4.7 | Contingencies | Lawsuits, regulatory proceedings |
| 5.1 | Risk management policy | Hedging, governance |

**Excluded**: Section 4.4 (individual lawsuit list — 148 pages of litigation, no analytical value), sections 6-13 (governance, compensation, share structure — not credit-relevant).

### Verified working on 8 companies (14/14 sections each)

Petrobras, Vale, Telefônica Brasil (Vivo), Ambev, Itaú Unibanco, Embraer, JBS, Suzano Holding.

### Chunk metadata preserved per FRE chunk

Each FRE chunk carries:
```python
{
    "document_id": "fre_{cnpj}_{year}",
    "filename": "{company_name} (FRE {year})",
    "source": "CVM/FRE",
    "cnpj": cnpj,
    "section": "4.1",
    "section_label": "Fatores de risco",
}
```

---

## Search and retrieval

**Vector store** (`app/search/vector_store.py`):
- NumPy matrix of L2-normalized 1024-dim vectors
- `np.argpartition` for O(n) top-k
- Cosine similarity via dot product

**BM25** (`app/search/keyword_search.py`):
- Pure Python, catches exact account codes ("3.01", "EBITDA") that semantic search underweights

**RRF reranker** (`app/search/reranker.py`):
- `score = Σ 1/(60 + rank)` — rank-only, no score normalization

**Year boosting** (in `app/routers/query.py`):
- When a year is specified, top_k is tripled (3×), year-matching chunks are boosted to front of context

**Company match check**:
- If results don't contain the company name in the filename → discard and trigger scrape
- Uses `company_name.split()[0]` partial match to avoid false positives

**Similarity threshold**: 0.7 (chunks below this are discarded and fall back to general LLM)

---

## In-memory state

`app/storage.py` holds two module-level globals:
- `documents`: dict — `{ document_id: { filename, pages, source, cnpj } }`
- `chunks`: list — each chunk is a dict with `text`, `filename`, `page_number`, `document_id`, `source`, `cnpj`, `section`, `section_label` (last two only for FRE)

Loaded from disk at startup (`app/persistence.py:load_state()`), saved after every scrape.

**Persistence files** (in `data/persist/`, gitignored):
- `chunks.json`, `vector_meta.json`, `bm25.json` — JSON
- `vectors.npy` — NumPy binary

---

## Configuration (`app/config.py`)

```python
MISTRAL_CHAT_MODEL = "mistral-small-latest"
MISTRAL_EMBED_MODEL = "mistral-embed"
CHUNK_SIZE = 2000
CHUNK_OVERLAP = 400
SIMILARITY_TOP_K = 5
SIMILARITY_THRESHOLD = 0.7
```

---

## Git state

**Current branch**: `formulario_de_referencia_retrieval`
**Base**: `main`

Branch adds:
- `app/scraper/fre_client.py` — 430 lines, the entire FRE scraper
- `app/scraper/pipeline.py` — extended with Step 3b (FRE), FRE chunking, FRE metadata tracking
- `requirements.txt` — added `python-docx==1.1.2` and `requests==2.32.3`
- `.gitignore` — updated to exclude all data cache dirs with `/*` patterns + `.gitkeep` exceptions
- `data/fre/.gitkeep`, `data/fre/docs/.gitkeep` — preserve directory structure on fresh clone

**Status**: All committed and clean. Branch is ready to push and merge into main.

**Key commits on branch**:
```
07b0bf5  wire FRE into pipeline: auto-scrape qualitative sections alongside DFP/ITR
4373b23  add FRE client: extract credit-relevant sections from CVM
```

---

## What's tested and confirmed working

1. **Petrobras risk factors** — scrapes 24 DFP/ITR + 13 FRE sections → 279 chunks → answers from FRE sections 4.1/5.1 with citations from "PETROBRAS (FRE 2025)"
2. **Vale risk factors** — 14/14 FRE sections, 345 chunks
3. **Vivo (Telefônica Brasil alias)** — alias resolved correctly, 14/14 sections
4. **Ambev** — 14/14 sections
5. **Itaú Unibanco** — 14/14 sections (new)
6. **Embraer** — 14/14 sections (new)
7. **JBS** — 14/14 sections (previously failed on 6 sections due to weird filenames — fixed by switching to XML tag-based extraction)
8. **Suzano Holding** — 14/14 sections (new)
9. **Year-specific queries** — "receita bruta 2023 de Vivo" correctly fetches and boosts 2023 chunks
10. **Source citations** — numbered chips in frontend, clickable to show source text panel

---

## Known decisions and why

| Decision | Reason |
|----------|--------|
| No LangChain/ChromaDB | Full control, no abstractions hiding behavior |
| `requests` not `httpx` for RAD CVM | httpx gets connection reset by rad.cvm.gov.br |
| Force `http://` → `https://` on FRE URLs | CVM metadata CSV has `http://` links but server requires HTTPS |
| XML tag-based section extraction | IR team filenames are inconsistent across companies — XML schema tags are CVM-mandated and reliable |
| pdfplumber not python-docx | Embedded files are PDFs (despite .docx filenames) — CVM converts them before embedding |
| FRE failure is non-fatal | Companies with no FRE (recently IPO'd, etc.) still get DFP/ITR data |
| Append-only knowledge base | Each company adds to the store permanently; 90-day staleness triggers re-scrape |
| LLM does NOT do math | All arithmetic in Python; LLM only extracts and summarizes |
| Extract over compute | Scrape documents where EBITDA/Capex are stated explicitly rather than computing from raw account codes |

---

## Next steps (in priority order)

### Step 1 — Push and merge the FRE branch (immediate)
```bash
git push origin formulario_de_referencia_retrieval
# Then open a PR and merge into main
```

### Step 2 — Dual output (extracted_data JSON)
Every query should return both:
1. Natural language answer (existing)
2. Structured `extracted_data` JSON: `{ metric, value, period, source, definition }`

This is the bridge toward the credit report template. Example output:
```json
{
  "answer": "O EBITDA ajustado da Petrobras em 2024 foi de R$ 204.234 milhões...",
  "extracted_data": {
    "EBITDA_ajustado": { "value": 204234, "unit": "M BRL", "period": "2024", "source": "FRE 2025 seção 2.5" }
  }
}
```
The LLM should NOT compute values — only extract what's explicitly stated in the documents.

### Step 3 — Friend's Yahoo Finance branch
Once their branch (market data: stock price, market cap, multiples) is ready, merge both into main. The knowledge base becomes: CVM structured data + FRE qualitative sections + Yahoo Finance market data.

### Step 4 — Credit report template
A YAML config for sector-specific benchmarks (editable by domain experts). Auto-populate a 1-page credit snapshot from the indexed data.

---

## What the frontend does

`app/static/index.html` — single-file chat UI:
- Connects to `/query/stream` via SSE
- Renders `progress` events as live status steps (e.g. "Baixando FRE 2025...")
- Renders `answer` event as markdown with numbered citation chips [1] [2]
- Citation chip click → shows source text panel with the raw chunk
- Source chip click is handled by a global click listener with `!e.target.closest(".source-chip")` guard to prevent panel closing on chip click

---

## Key error patterns encountered (and fixed)

| Error | Fix |
|-------|-----|
| `KeyError 'SG_ACAO'` in CVM registry | Ticker column doesn't exist. Use `CD_CVM` instead |
| "Banco do Brasil" matched wrong company | Token filter was `len(t) > 2`, dropped "do". Changed to `len(t) > 1` |
| Axia Energia not found | Extractor only knew famous companies. Changed prompt to "Extract ANY company name" |
| Vivo answer: "I found Telefônica but you asked Vivo" | Added `alias_hint` injected into RAG prompt |
| Source chips not clickable | Global click listener closed panel. Added `!e.target.closest(".source-chip")` guard |
| 2023 queries returned 2024 data | Added `extract_year()`, year boosting, 3× top-k for year-specific queries |
| FRE: connection reset | httpx blocked by RAD server. Use `requests.Session` |
| FRE: "File is not a zip file" | Embedded files are PDFs despite `.docx` extension. Use pdfplumber |
| Vale FRE: 0 sections | Regex matched on filename start — Vale uses `"FRE 2025 item 2.1.pdf"`. Fixed by switching to XML tag-based extraction |
| Telefônica FRE: 4/14 sections | Zero-padded filenames (`4.01` not `4.1`). Fixed by XML tag approach |
| JBS FRE: 8/14 sections | Filename typos (`"1. 2 - JBS..."`, `"5,5,1"`). Fixed by XML tag approach |
