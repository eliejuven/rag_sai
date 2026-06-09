# RAG Pipeline — Brazilian Financial Intelligence

A Retrieval-Augmented Generation (RAG) system that answers questions about Brazilian listed companies using official CVM filings. It automatically fetches financial statements when data is missing, streams live progress to the user, and grounds every answer in real source documents with clickable citations.

Built with FastAPI and Mistral AI, with no external RAG or vector search libraries.

---

## What it does

**Two modes:**

1. **PDF upload** — Upload any PDF (annual report, Excel converted to PDF, contract) and ask questions about it. The system extracts text, detects tables, and indexes the content.

2. **Auto-scrape** — Ask a question about any Brazilian listed company (B3). If the data is not already indexed, the system automatically fetches the company's official financial statements from CVM (Brazil's financial regulator), indexes them, and answers — while streaming live progress to the chat.

**Key behaviors:**
- Resolves brand names and aliases: "Vivo" → Telefônica Brasil, "Magalu" → Magazine Luiza
- Detects the year mentioned in the question and fetches that specific year if missing
- Caches all scraped data to disk — survives server restarts, grows over time
- Cites sources with clickable chips that show the exact text used to generate the answer
- Falls back to general LLM knowledge when documents are genuinely not relevant, clearly labeled

---

## System Architecture

```
User question
      │
      ▼
Intent detection          Is this a question or casual chat?
      │
      ▼
Company + year extraction  "Vivo 2023" → "Telefônica Brasil", year=2023
      │
      ▼
Vector + keyword search   Is the answer already in the knowledge base?
      │
      ├─ Wrong company or year missing?
      │         │
      │         ▼
      │   CVM Auto-Scraper ──────────────────────────────────────┐
      │         │                                                 │
      │         ├─ Registry lookup (name → CNPJ)                 │
      │         ├─ Download DFP/ITR ZIP from CVM                 │
      │         ├─ Extract company rows from CSV                  │
      │         ├─ Format as structured text                      │
      │         ├─ Chunk → embed → store                         │
      │         └─ Persist to disk                               │
      │                                                          │
      ▼                                                          │
Table extraction skill    Inject number-reading rules if tabular ◄┘
      │
      ▼
RAG generation            Answer ONLY from retrieved chunks, cite sources
      │
      ▼
SSE stream                Live progress + final grounded answer with citations
```

---

## Key Components

### 1. CVM Scraper (`app/scraper/`)

**Registry lookup** (`cvm_registry.py`): Downloads the official CVM company registry (`cad_cia_aberta.csv`) and caches it. Maps free-text company names to CNPJ using 3-tier fuzzy matching: exact trade name → exact legal name → partial token match ranked by score. Normalizes accents and punctuation before matching.

**Financial data client** (`cvm_client.py`): Downloads DFP (annual) and ITR (quarterly YTD) ZIP files from CVM's public data portal. Each ZIP (~13MB) contains data for all ~500 listed companies. Reads specific CSVs from inside the ZIP without extracting to disk, filters to the target company's CNPJ, applies scale multipliers (MIL → ×1000), and formats 4 statement types as structured text:
- DRE (Income Statement)
- BPA (Balance Sheet — Assets)
- BPP (Balance Sheet — Liabilities)
- DFC (Cash Flow Statement)

**Pipeline** (`pipeline.py`): Orchestrates the full flow. Staleness check: skips re-scraping if data is fresh (< 90 days) AND the requested year is already indexed. Builds year lists dynamically — if the user asks about 2023 and we only have 2024/2025, it adds 2023 to the fetch list. Reports every step via async progress callback for SSE streaming.

### 2. PDF Ingestion (`app/ingestion/`)

**PDF parser** (`pdf_parser.py`): Uses `pdfplumber` for word-level text extraction. Reconstructs table structure from word positions — handles Excel-converted PDFs where columns are positioned text rather than HTML tables. Falls back gracefully on non-tabular PDFs.

**Chunker** (`chunker.py`): Recursive character splitter. Tries to split at natural boundaries (paragraph → line → sentence → word). Chunk size 2000 chars, overlap 400 chars. Preserves page number metadata for citations.

### 3. Embeddings (`app/embeddings/`)

Calls Mistral's `mistral-embed` model to convert text to 1024-dimensional vectors. Batches automatically (16 texts per API call, CVM's limit). The same model embeds both documents and queries so they live in the same semantic space.

### 4. Search (`app/search/`)

**Vector store** (`vector_store.py`): NumPy matrix of L2-normalized vectors. Search is a single matrix-vector dot product (= cosine similarity). Uses `np.argpartition` for O(n) top-k selection instead of full sort.

**BM25 keyword search** (`keyword_search.py`): Scores documents by term frequency × inverse document frequency with length normalization. Catches exact matches for account codes like "3.01" or "EBITDA" that semantic search might underweight.

**Reranker** (`reranker.py`): Reciprocal Rank Fusion merges semantic and keyword results. Formula: `RRF(d) = Σ 1/(k + rank)` with k=60. Score-agnostic — uses only rank positions, avoiding the need to normalize different score scales.

### 5. Query Processing (`app/query/`)

**Intent detection** (`intent.py`): LLM classifies query as "search" or "chat". Prevents retrieval for greetings and general questions.

**Query transformation** (`transform.py`): Rewrites vague queries before embedding. "What's the leverage?" → "What is the debt-to-EBITDA leverage ratio mentioned in the financial statements?"

**Company extractor** (`company_extractor.py`): LLM extracts company name from the question and resolves aliases (Vivo → Telefônica Brasil, PETR4 → Petrobras, Magalu → Magazine Luiza). Returns any company name mentioned, not just famous ones. Also detects when the official name differs from what the user typed — used to inject an alias hint into the RAG prompt so the LLM doesn't say "I see Telefônica Brasil data but you asked about Vivo."

**Year extraction** (`company_extractor.py`): Pure regex — finds any 4-digit year (2000–2099) in the question. Used to trigger targeted re-scraping and boost year-matching chunks in retrieval.

### 6. Generation (`app/generation/`)

**LLM client** (`llm.py`): Wrapper around Mistral's chat completion API with configurable temperature.

**Prompts** (`prompts.py`): RAG prompt injects retrieved chunks as numbered context. Accepts optional `alias_hint` (e.g. "Vivo refers to Telefônica Brasil") and builds it into the prompt header.

**Skills** (`skills.py`): Additional instructions injected into the system prompt when tabular/financial data is detected in retrieved chunks. Teaches the LLM to:
- Recognize space-as-thousands-separator: `16 683` = 16,683
- Reconstruct PDF split artifacts: `1 6 683` = 16,683
- Interpret parentheses as negative: `(6 614)` = −6,614
- Apply scale from table header (R$ Mil, R$ Milhão)
- Never apply monetary scale to percentage rows

### 7. Persistence (`app/persistence.py`)

Saves the full RAG state to `data/persist/`:
- `chunks.json` — all text chunks and metadata
- `vectors.npy` — NumPy vector matrix
- `vector_meta.json` — chunk index mapping
- `bm25.json` — BM25 internal state

Called at server startup (`load_state()`) and after every ingestion or scrape (`save_state()`). The knowledge base accumulates over time — each new company asked about gets added permanently.

### 8. SSE Streaming (`app/routers/query.py`)

`POST /query/stream` uses `asyncio.Queue` + FastAPI `StreamingResponse`. The pipeline runs as a background task pushing events to the queue; the generator yields them as `text/event-stream`. Three event types:
- `progress` — live status message shown in chat
- `answer` — final grounded response with chunks
- `error` — step-specific failure message

The existing `POST /query` endpoint is unchanged for non-streaming clients.

---

## Project Structure

```
app/
├── main.py                      # FastAPI app + startup state loading
├── config.py                    # API keys, chunk size, similarity threshold
├── models.py                    # Pydantic request/response schemas
├── storage.py                   # In-memory documents and chunks
├── persistence.py               # Save/load full state to disk
├── scraper/
│   ├── cvm_registry.py          # Company name → CNPJ lookup
│   ├── cvm_client.py            # Download + parse CVM DFP/ITR data
│   └── pipeline.py              # Orchestrate scrape → chunk → embed → store
├── ingestion/
│   ├── pdf_parser.py            # PDF text + table extraction (pdfplumber)
│   └── chunker.py               # Recursive text chunking with overlap
├── embeddings/
│   └── client.py                # Mistral embedding API (batched)
├── search/
│   ├── vector_store.py          # NumPy cosine similarity search
│   ├── keyword_search.py        # BM25 keyword search
│   └── reranker.py              # Reciprocal Rank Fusion
├── query/
│   ├── intent.py                # Intent classification (search vs chat)
│   ├── transform.py             # Query rewriting for better retrieval
│   └── company_extractor.py     # Company name + year extraction from question
├── generation/
│   ├── llm.py                   # Mistral chat completion client
│   ├── prompts.py               # RAG prompt builder with alias hint support
│   └── skills.py                # Table/financial number reading instructions
├── routers/
│   ├── ingest.py                # POST /ingest — PDF upload
│   └── query.py                 # POST /query + POST /query/stream (SSE)
└── static/
    └── index.html               # Chat UI — SSE streaming, citations, source panel

data/                            # Runtime data (gitignored)
├── cvm_registry.csv             # Cached CVM company registry
├── scraped_companies.json       # Scrape metadata (timestamp, years indexed)
├── dfp/                         # Cached annual statement ZIPs
├── itr/                         # Cached quarterly statement ZIPs
└── persist/                     # Persisted vector store and chunks
```

---

## Setup

### Prerequisites

- Python 3.12+
- A [Mistral AI](https://console.mistral.ai/) API key

### Install

```bash
git clone https://github.com/eliejuven/rag_sai.git
cd rag_sai

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

echo "MISTRAL_API_KEY=your_key_here" > .env
```

### Run

```bash
source .venv/bin/activate
python3 -m uvicorn app.main:app --reload --port 8001
```

Open **http://localhost:8001** in your browser.

---

## Usage

### Ask about a Brazilian listed company (no upload needed)

Just type a question. The system automatically fetches official CVM data:

```
Qual foi o lucro líquido da Petrobras em 2024?
Qual é a receita bruta da Axia Energia em 2023?
Me mostre o balanço patrimonial do Banco do Brasil.
```

The first question about a company takes 20–30 seconds (downloading CVM data). All subsequent questions about the same company are instant — data is cached locally and persisted across restarts.

**Supported aliases:** Vivo → Telefônica Brasil, Magalu → Magazine Luiza, Itaú → Itaú Unibanco, BB → Banco do Brasil, PETR4 → Petrobras, and any other B3-listed company by name.

### Upload a PDF

Click the paperclip icon to upload one or more PDFs. The system extracts text and tables, indexes them, and adds them to the knowledge base alongside any CVM data already loaded.

### Citations

Every grounded answer includes numbered source chips `[1] filename — p.X`. Click any chip to see the exact text the LLM used to generate the answer.

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/ingest` | Upload PDF files (multipart form, field: `files`) |
| `POST` | `/query` | Ask a question — returns JSON `QueryResponse` |
| `POST` | `/query/stream` | Ask a question — returns SSE stream with live progress |
| `GET` | `/health` | Health check |
| `GET` | `/docs` | Swagger UI |

### SSE Event format (`/query/stream`)

```json
{"type": "progress", "message": "Baixando demonstrações financeiras..."}
{"type": "answer",   "answer": "...", "grounded": true, "chunks": [...]}
{"type": "error",    "message": "Empresa não encontrada no cadastro CVM."}
```

---

## Data Sources

All financial data comes from **CVM** (Comissão de Valores Mobiliários), Brazil's equivalent of the SEC. Every B3-listed company is legally required to file standardized financial statements there.

| Source | URL | What it contains |
|--------|-----|-----------------|
| Company registry | `dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/` | Name, CNPJ, sector, status for all ~500 companies |
| DFP (annual) | `dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/` | Annual income statement, balance sheet, cash flow |
| ITR (quarterly) | `dados.cvm.gov.br/dados/CIA_ABERTA/DOC/ITR/DADOS/` | Quarterly YTD statements (Q1, Q2, Q3) |

Data is free, official, and requires no API key.

---

## Libraries

| Library | Purpose |
|---------|---------|
| FastAPI | Web framework |
| Uvicorn | ASGI server |
| pdfplumber | PDF text and table extraction |
| pandas | CSV parsing for CVM data files |
| NumPy | Vector storage and cosine similarity |
| httpx | HTTP client (Mistral API + CVM downloads) |
| python-dotenv | Environment variable management |
| python-multipart | File upload support |

No external libraries are used for search, RAG, vector storage, or embeddings.

---

## Design Decisions

**Progressive knowledge base.** The system accumulates data over time. Every company asked about gets scraped once and persisted. The second question about the same company is instant. The 90-day staleness threshold aligns with CVM's quarterly filing cycle.

**Year-aware retrieval.** When a specific year is mentioned, the system (1) checks if that year is indexed and re-scrapes if not, (2) fetches 3× more candidates from the vector store, (3) scans the full chunk store for year-matching chunks not in the top-k, and (4) boosts year-matching chunks to the front of the context window.

**Alias injection.** When a brand name is resolved to an official name (Vivo → Telefônica Brasil), the difference is injected as a hint into the RAG prompt. Without this, the LLM sees "TELEFÔNICA BRASIL" in the chunks and says the Vivo data is unavailable.

**No conclusions without sources.** The system prompt instructs the LLM to answer only from the provided context, cite chunk numbers for every claim, and explicitly say when information is not available — rather than estimating or extrapolating.

**Hybrid search.** Semantic search (cosine similarity) captures meaning but can miss exact terms. BM25 catches precise matches for account codes like "3.01" or "EBITDA". RRF merges both without normalizing their different score scales.

**Table extraction skill.** Injected only when tabular data is detected in retrieved chunks. Teaches the LLM the specific number formats used in CVM filings: space-as-thousands-separator, parentheses-as-negative, PDF split artifacts, scale labels.
