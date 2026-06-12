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
python3 test_market_data.py
python3 test_bcb_client.py
```

The app is available at `http://localhost:8001`. Swagger UI at `/docs`.

## Architecture

This is a RAG pipeline for Brazilian listed-company financial data. All vector search, BM25, and embedding logic is built from scratch — no LangChain, ChromaDB, or similar libraries.

### Request flow

Every question goes through `POST /query/stream` (SSE) in `app/routers/query.py`. There is also a non-streaming `POST /query` endpoint with identical logic but a single JSON response. A manual `POST /ingest` endpoint in `app/routers/ingest.py` accepts PDF uploads and adds them directly to the store.

1. **Intent** (`app/query/intent.py`) — LLM classifies into four intents: `"search"`, `"market"`, `"macro"`, or `"chat"`. Chat questions return a direct LLM answer. Market questions route to the Yahoo Finance branch (see below). Macro questions route to the BCB macro branch (see below). Only `"search"` enters the RAG pipeline.
2. **Company + year extraction** (`app/query/company_extractor.py`) — LLM extracts and resolves aliases (Vivo → Telefônica Brasil). Year is extracted by regex. If the resolved name differs from what the user typed, an `alias_hint` string is built and injected into the RAG prompt later so the LLM doesn't say "I found Telefônica Brasil but you asked about Vivo."
3. **Query rewriting** (`app/query/transform.py`) — Before embedding, the question is rewritten by an LLM to expand abbreviations and make implicit context explicit, improving retrieval quality.
4. **First search attempt** — Vector + BM25 hybrid search on the existing in-memory store. Results are discarded if they belong to a different company or don't cover the requested year.
5. **Auto-scrape** (`app/scraper/pipeline.py`) — If search fails, `scrape_and_ingest()` resolves the company in the CVM registry, downloads DFP/ITR ZIP files from CVM's public portal, chunks+embeds the data, and persists everything. Each step emits a progress message via an async callback that the SSE queue forwards to the browser.
6. **Generation** (`app/generation/`) — Reranked chunks are injected into a RAG prompt. If tabular/financial data is detected in the chunks, `skills.py` dynamically appends a detailed `TABLE_EXTRACTION_SKILL` to the system prompt with rules for reading space-thousands separators, parenthetical negatives, and PDF rendering artifacts.

### Market data branch

When intent is `"market"`, the pipeline bypasses CVM and the vector store entirely:

- `app/scraper/market_data.py` holds a static `TICKER_MAP` (~50 major B3 companies) mapping normalized names to tickers with the `.SA` suffix (e.g. `PETR4.SA`).
- `resolve_ticker(company_name)` normalizes accents/case and looks up the map; returns `None` if not found.
- `fetch_market_data(ticker)` is **synchronous** — call it via `asyncio.to_thread()` from async code. It uses `yfinance` (no API key needed) to fetch a snapshot (price, market cap, P/E, valuation ratios, profitability metrics) plus 2-year weekly price history.
- `format_market_context()` formats the result as a Portuguese text block injected into `build_market_prompt()`.
- If the ticker isn't in `TICKER_MAP`, the pipeline falls back to a general LLM answer.

### BCB macro branch

When intent is `"macro"`, the pipeline fetches from the Brazilian Central Bank (BCB) API:

- `app/scraper/bcb_client.py` fetches 6 SGS time-series (Selic, IPCA, BRL/USD, IGPM, unemployment, GDP) and Focus Report consensus forecasts via `httpx`. All fetches run in parallel with `asyncio.gather`.
- Results are cached to `data/bcb_cache.json` with a **366-day TTL** (persists across restarts). `get_macro_data()` reads from cache when fresh; fetches and writes on first use or expiry; falls back to stale cache if the live fetch fails.
- `format_macro_sections()` returns 5 named sections (`[1] Política Monetária`, `[2] Inflação`, `[3] Câmbio`, `[4] Atividade Econômica`, `[5] Focus Report`) passed to `build_macro_prompt()` for numbered citations.
- **Enrichment**: every RAG and market response also receives a one-line BCB snapshot (`get_macro_snapshot_line()`) as a numbered section appended to the prompt. A `"BCB — Macro Snapshot"` chip is only included in the response when the LLM's answer actually cites that section number.

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
