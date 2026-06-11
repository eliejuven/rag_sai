# Market Data Feature — Design Spec

**Date:** 2026-06-10  
**Branch:** `feature/market_data`  
**Status:** Approved

---

## Goal

Allow users to ask about a Brazilian listed company's current market price, market cap, and historical price performance. Data is fetched on-demand from Yahoo Finance (yfinance, free, no API key, `.SA` suffix for B3 tickers) and injected directly into the LLM prompt. Never indexed or persisted.

---

## New Files

### `app/scraper/market_data.py`

Owns all yfinance logic. Three public functions:

**`resolve_ticker(company_name: str) -> str | None`**  
Normalizes `company_name` (lowercase, strip accents) and looks it up in `TICKER_MAP`. Returns the B3 ticker (e.g. `"PETR4.SA"`) or `None` if unknown.

**`TICKER_MAP: dict[str, str]`**  
Curated ~50-entry mapping from normalized company name to B3 ticker. Covers the most common B3 stocks. Examples:
- `"petrobras"` → `"PETR4.SA"`
- `"itau unibanco"` → `"ITUB4.SA"`
- `"vale"` → `"VALE3.SA"`
- `"ambev"` → `"ABEV3.SA"`
- `"bradesco"` → `"BBDC4.SA"`
- `"banco do brasil"` → `"BBAS3.SA"`
- `"magazine luiza"` → `"MGLU3.SA"`
- `"telefonica brasil"` → `"VIVT3.SA"`
- (etc. for top ~50 B3 stocks)

**`fetch_market_data(ticker: str) -> dict`**  
Synchronous function (yfinance is blocking). Called via `asyncio.to_thread(fetch_market_data, ticker)` from the async router.  
Calls `yf.Ticker(ticker).info` and `yf.Ticker(ticker).history(period="2y")`. Resamples history to **weekly** (last trading day of each week) to cap the context block at ~100 rows rather than ~500. Returns:
```python
{
    "snapshot": {
        "price": float | None,
        "market_cap": float | None,
        "pe_ratio": float | None,
        "week_52_high": float | None,
        "week_52_low": float | None,
        "currency": str,
    },
    "history": pd.DataFrame  # weekly resampled: Date, Close, Volume
}
```

**`format_market_context(data: dict, company_name: str, ticker: str) -> str`**  
Formats the dict as a human-readable Portuguese text block for prompt injection. Skips missing fields gracefully. Example output:
```
=== Dados de Mercado: Petrobras (PETR4.SA) ===
Preço atual: R$ 38,42
Market Cap: R$ 497,3 bi
P/L: 4,8
Máxima 52 semanas: R$ 44,10
Mínima 52 semanas: R$ 30,15

Histórico de Preços (últimos 2 anos):
Data        Fechamento   Volume
2024-01-02  R$ 35,10     42.3M
...
```

---

### `test_market_data.py`

Standalone async test script (same pattern as `test_pipeline.py`, `test_cvm_client.py`, etc.). Tests:
- `resolve_ticker()` with known and unknown company names
- `fetch_market_data()` with a real ticker (e.g. `PETR4.SA`)
- `format_market_context()` output sanity check

---

## Modified Files

### `app/query/intent.py`

Add `"market"` as a third intent category. The LLM classification prompt gains examples of market-intent queries:
- Preço, cotação, valor de mercado, market cap
- Histórico de preços, valorização, desvalorização
- Máxima/mínima, 52 semanas
- "Quanto vale", "como está a ação"

Returns one of: `"chat"` | `"search"` | `"market"`

### `app/routers/query.py`

`run_pipeline()` gets a new branch after intent detection:

```
if intent == "market":
    extract_company()                      → company_name or None
    resolve_ticker()                       → ticker or None (→ fallback if None)
    asyncio.to_thread(fetch_market_data)   → data dict (→ fallback on exception)
    format_market_context()                → text block
    build_market_prompt()                  → user message
    chat_completion()                      → answer
    emit SSE answer (grounded=True, chunks=[])
```

Fallback in both failure cases (ticker unknown, yfinance error): emit a Portuguese warning message, then call `chat_completion(GENERAL_SYSTEM_PROMPT, question)` and emit a non-grounded answer — identical to the existing scrape-failure fallback pattern.

### `app/generation/prompts.py`

Add `build_market_prompt(question: str, market_text: str, alias_hint: str | None) -> str`.  
Assembles the user message with the formatted yfinance block as context, plus the question. No RAG chunks. Reuses `RAG_SYSTEM_PROMPT` as the system prompt (answer only from provided context).

---

## Data Flow

```
User question (SSE)
  ↓
detect_intent()           → "market"
  ↓
extract_company()         → "Petrobras"
  ↓
resolve_ticker()          → "PETR4.SA"
  ↓
fetch_market_data()       → {snapshot, history}
  ↓
format_market_context()   → text block
  ↓
build_market_prompt()     → user message
  ↓
chat_completion()         → grounded answer
  ↓
SSE "answer" event        grounded=True, chunks=[]
```

---

## Error Handling

| Case | Behavior |
|------|----------|
| `resolve_ticker()` returns `None` | Emit `"Ticker de [company] não encontrado."` → general LLM fallback |
| `fetch_market_data()` raises | Emit `"Erro ao buscar dados de mercado."` → general LLM fallback |
| `ticker.info` returns sparse dict | `format_market_context()` skips `None` fields, returns partial snapshot |
| No company extracted | Emit `"Nenhuma empresa detectada."` → general LLM fallback |

---

## What Is NOT Changing

- `storage.py`, `persistence.py` — market data is never indexed
- Vector store, BM25 index — untouched
- CVM scraping pipeline — untouched
- `app/config.py` — no new config knobs needed

---

## Dependencies

Add `yfinance` to `requirements.txt`. No API key required.
