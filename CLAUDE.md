# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
echo "MISTRAL_API_KEY=your_key" > .env

# Run server
python3 -m uvicorn app.main:app --reload --port 8001

# Run individual test scripts (no test framework — each is a standalone async script)
python3 test_pipeline.py
python3 test_registry.py
python3 test_cvm_client.py
python3 test_company_extractor.py
python3 test_persistence.py
```

The app is available at `http://localhost:8001`. Swagger UI at `/docs`.

## Architecture

This is a RAG pipeline for Brazilian listed-company financial data. All vector search, BM25, and embedding logic is built from scratch — no LangChain, ChromaDB, or similar libraries.

### Request flow

Every question goes through `POST /query/stream` (SSE) in `app/routers/query.py`:

1. **Intent** (`app/query/intent.py`) — LLM classifies "search" vs "chat". Chat questions skip retrieval entirely.
2. **Company + year extraction** (`app/query/company_extractor.py`) — LLM extracts and resolves aliases (Vivo → Telefônica Brasil). Year is extracted by regex. If the resolved name differs from what the user typed, an `alias_hint` string is built and injected into the RAG prompt later so the LLM doesn't say "I found Telefônica Brasil but you asked about Vivo."
3. **First search attempt** — Vector + BM25 hybrid search on the existing in-memory store. Results are discarded if they belong to a different company or don't cover the requested year.
4. **Auto-scrape** (`app/scraper/pipeline.py`) — If search fails, `scrape_and_ingest()` resolves the company in the CVM registry, downloads DFP/ITR ZIP files from CVM's public portal, chunks+embeds the data, and persists everything. Each step emits a progress message via an async callback that the SSE queue forwards to the browser.
5. **Generation** (`app/generation/`) — Reranked chunks are injected into a RAG prompt. If tabular/financial data is detected, extra number-parsing instructions are appended (`skills.py`).

### In-memory state

`app/storage.py` holds two module-level globals: `documents` (dict) and `chunks` (list of dicts). All search modules operate on these directly. The state is loaded from disk at startup (`app/persistence.py:load_state()`) and saved after every ingestion or scrape (`save_state()`).

### Search

`app/search/vector_store.py` — NumPy matrix of L2-normalized 1024-dim vectors. Search is a matrix-vector dot product (cosine similarity). Uses `np.argpartition` for O(n) top-k.

`app/search/keyword_search.py` — Pure-Python BM25. Catches exact account-code matches ("3.01", "EBITDA") that semantic search underweights.

`app/search/reranker.py` — Reciprocal Rank Fusion: `score = Σ 1/(k + rank)` with k=60. Rank-only, no score normalization needed.

When a year is specified, top-k is tripled and year-matching chunks are boosted to the front of the context window before trimming back to `SIMILARITY_TOP_K`.

### CVM scraper

`app/scraper/cvm_registry.py` — Downloads `cad_cia_aberta.csv` and resolves free-text names to CNPJ via 3-tier fuzzy matching (exact trade name → exact legal name → partial token match). Normalizes accents before matching.

`app/scraper/cvm_client.py` — Downloads DFP (annual) and ITR (quarterly) ZIP files from CVM's public data portal (~13 MB each, covering all ~500 listed companies). Reads target CSVs directly from inside the ZIP without extracting to disk. Applies scale multipliers and formats four statement types as structured text (DRE, BPA, BPP, DFC).

Staleness threshold is 90 days (`STALENESS_DAYS` in `pipeline.py`). A company is re-scraped if: never indexed, data is older than 90 days, or a requested year is not in `metadata[cnpj]["dfp_years"]`.

### Persistence

`data/persist/` holds `chunks.json`, `vectors.npy`, `vector_meta.json`, `bm25.json`. The knowledge base is append-only — each new company adds to it permanently. `data/scraped_companies.json` tracks scrape timestamps and indexed years per CNPJ.

### Configuration

All tunable parameters are in `app/config.py`: chunk size (2000), overlap (400), top-k (5), similarity threshold (0.7), and model names (`mistral-small-latest` for chat, `mistral-embed` for embeddings).

### Frontend

`app/static/index.html` is a single-file chat UI that connects to `/query/stream`. It renders SSE `progress` events as live status steps, `answer` events as the final response with numbered citation chips, and shows the source text panel on chip click.
