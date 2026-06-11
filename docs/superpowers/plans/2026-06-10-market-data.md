# Market Data Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add on-demand Yahoo Finance market data (current price snapshot + 2-year weekly history) to the RAG pipeline via a new `"market"` intent, injecting data directly into the LLM prompt without persisting anything.

**Architecture:** A new `app/scraper/market_data.py` owns all yfinance logic (ticker map, fetch, format). `intent.py` gains a third `"market"` category. `query.py`'s `run_pipeline()` gets a new branch for market queries that bypasses the vector store entirely. Market data is fetched synchronously and dispatched with `asyncio.to_thread()` from the async router.

**Tech Stack:** `yfinance` (free, no API key), `pandas` (already in requirements), existing Mistral LLM client.

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `app/scraper/market_data.py` | TICKER_MAP, resolve_ticker, fetch_market_data, format_market_context |
| Create | `test_market_data.py` | Standalone async test script for market_data module |
| Modify | `requirements.txt` | Add yfinance |
| Modify | `app/query/intent.py` | Add "market" intent category |
| Modify | `app/generation/prompts.py` | Add build_market_prompt function |
| Modify | `app/routers/query.py` | Add market branch in run_pipeline(), update imports |

---

## Task 1: Add yfinance to requirements.txt

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add yfinance**

Open `requirements.txt` and add this line at the end:
```
yfinance>=0.2.54
```

Final file should look like:
```
fastapi==0.115.12
uvicorn==0.34.2
python-multipart==0.0.20
pdfplumber==0.11.4
pandas==2.2.3
numpy==2.2.4
httpx==0.28.1
python-dotenv==1.1.0
yfinance>=0.2.54
```

- [ ] **Step 2: Install**

```bash
source .venv/bin/activate
pip install yfinance
```

Expected: yfinance and its dependencies (multitasking, peewee, etc.) install without errors.

- [ ] **Step 3: Verify**

```bash
python3 -c "import yfinance as yf; print(yf.__version__)"
```

Expected: prints a version string like `0.2.54`.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "feat: add yfinance dependency for market data"
```

---

## Task 2: Create app/scraper/market_data.py

**Files:**
- Create: `app/scraper/market_data.py`

- [ ] **Step 1: Write test_market_data.py first (it will fail — module doesn't exist yet)**

Create `test_market_data.py` in the project root:

```python
"""Standalone test script for app/scraper/market_data.py"""
import asyncio
import sys


def test_resolve_ticker():
    from app.scraper.market_data import resolve_ticker

    assert resolve_ticker("Petrobras") == "PETR4.SA"
    assert resolve_ticker("Itaú Unibanco") == "ITUB4.SA"
    assert resolve_ticker("Vale") == "VALE3.SA"
    assert resolve_ticker("Ambev") == "ABEV3.SA"
    assert resolve_ticker("Banco do Brasil") == "BBAS3.SA"
    assert resolve_ticker("PETROBRAS") == "PETR4.SA"           # case insensitive
    assert resolve_ticker("Empresa Desconhecida") is None      # unknown → None
    print("✓ resolve_ticker")


def test_fetch_market_data():
    from app.scraper.market_data import fetch_market_data

    data = fetch_market_data("PETR4.SA")

    assert "snapshot" in data
    assert "history" in data

    snap = data["snapshot"]
    for key in ("price", "market_cap", "pe_ratio", "week_52_high", "week_52_low", "currency"):
        assert key in snap, f"Missing snapshot key: {key}"

    history = data["history"]
    assert list(history.columns) == ["Date", "Close"], f"Unexpected columns: {history.columns.tolist()}"
    assert len(history) <= 105, f"Expected ≤105 weekly rows, got {len(history)}"
    print(f"✓ fetch_market_data  price={snap['price']}  history_rows={len(history)}")


def test_format_market_context():
    from app.scraper.market_data import fetch_market_data, format_market_context

    data = fetch_market_data("PETR4.SA")
    text = format_market_context(data, "Petrobras", "PETR4.SA")

    assert "Petrobras" in text
    assert "PETR4.SA" in text
    assert "Histórico" in text
    print("✓ format_market_context")
    print(text[:400])
    print("...")


async def main():
    print("=== test_market_data.py ===\n")
    test_resolve_ticker()
    test_fetch_market_data()
    test_format_market_context()
    print("\nAll tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run to confirm it fails**

```bash
python3 test_market_data.py
```

Expected: `ModuleNotFoundError: No module named 'app.scraper.market_data'`

- [ ] **Step 3: Create app/scraper/market_data.py**

```python
"""
Market Data

Fetches real-time and historical market data for Brazilian listed companies
from Yahoo Finance (yfinance, free, no API key).

B3 tickers use the .SA suffix (e.g. PETR4.SA).

Synchronous — call fetch_market_data via asyncio.to_thread() from async code.
"""

import re
import unicodedata

import pandas as pd
import yfinance as yf


# ---------------------------------------------------------------------------
# Ticker map — normalized company name → B3 ticker
# ---------------------------------------------------------------------------

TICKER_MAP: dict[str, str] = {
    # Banks
    "itau unibanco": "ITUB4.SA",
    "itau": "ITUB4.SA",
    "bradesco": "BBDC4.SA",
    "banco bradesco": "BBDC4.SA",
    "banco bradeco": "BBDC4.SA",          # matches typo in company_extractor prompt
    "banco do brasil": "BBAS3.SA",
    "btg pactual": "BPAC11.SA",
    "btg": "BPAC11.SA",
    "santander brasil": "SANB11.SA",
    "santander": "SANB11.SA",
    # Energy / Oil
    "petrobras": "PETR4.SA",
    "petroleo brasileiro": "PETR4.SA",
    "eletrobras": "ELET3.SA",
    "eneva": "ENEV3.SA",
    "engie brasil": "EGIE3.SA",
    "cosan": "CSAN3.SA",
    # Mining / Steel
    "vale": "VALE3.SA",
    "gerdau": "GGBR4.SA",
    "usiminas": "USIM5.SA",
    "csn": "CSNA3.SA",
    "companhia siderurgica nacional": "CSNA3.SA",
    # Pulp / Paper
    "suzano": "SUZB3.SA",
    # Food / Beverage
    "ambev": "ABEV3.SA",
    "jbs": "JBSS3.SA",
    "brf": "BRFS3.SA",
    "brfoods": "BRFS3.SA",
    # Telecom
    "telefonica brasil": "VIVT3.SA",
    "vivo": "VIVT3.SA",
    "tim brasil": "TIMS3.SA",
    "tim": "TIMS3.SA",
    # Tech / Industrials
    "totvs": "TOTS3.SA",
    "weg": "WEGE3.SA",
    "embraer": "EMBR3.SA",
    # Retail
    "magazine luiza": "MGLU3.SA",
    "magalu": "MGLU3.SA",
    "lojas renner": "LREN3.SA",
    "renner": "LREN3.SA",
    "grupo pao de acucar": "PCAR3.SA",
    "grupo casas bahia": "BHIA3.SA",
    "casas bahia": "BHIA3.SA",
    "americanas": "AMER3.SA",
    # Healthcare
    "raia drogasil": "RADL3.SA",
    "hypera": "HYPE3.SA",
    "hapvida": "HAPV3.SA",
    "fleury": "FLRY3.SA",
    # Logistics / Transport
    "rumo": "RAIL3.SA",
    "localiza": "RENT3.SA",
    "vamos": "VAMO3.SA",
    # Utilities
    "sabesp": "SBSP3.SA",
    "equatorial": "EQTL3.SA",
    "cpfl": "CPFE3.SA",
    "cemig": "CMIG4.SA",
    "copel": "CPLE6.SA",
    "taesa": "TAEE11.SA",
    # Real estate / Construction
    "cyrela": "CYRE3.SA",
    "mrv": "MRVE3.SA",
    # Education
    "cogna": "COGN3.SA",
    "yduqs": "YDUQ3.SA",
    # Insurance / Finance
    "porto seguro": "PSSA3.SA",
    "porto": "PSSA3.SA",
    # Exchange
    "b3": "B3SA3.SA",
    # Consumer / Fashion / Other
    "natura": "NTCO3.SA",
    "natura co": "NTCO3.SA",
    "arezzo": "ARZZ3.SA",
    "ultrapar": "UGPA3.SA",
}


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def resolve_ticker(company_name: str) -> str | None:
    """Return the B3 ticker for a company name, or None if not in TICKER_MAP."""
    return TICKER_MAP.get(_normalize(company_name))


def fetch_market_data(ticker: str) -> dict:
    """
    Fetch snapshot + 2-year weekly price history from Yahoo Finance.

    Synchronous — call via asyncio.to_thread(fetch_market_data, ticker) from async code.

    Returns:
        {
            "snapshot": {"price", "market_cap", "pe_ratio",
                         "week_52_high", "week_52_low", "currency"},
            "history": pd.DataFrame  columns=["Date", "Close"]  (weekly, ≤105 rows)
        }
    """
    t = yf.Ticker(ticker)
    info = t.info

    snapshot = {
        "price": info.get("currentPrice") or info.get("regularMarketPrice"),
        "market_cap": info.get("marketCap"),
        "pe_ratio": info.get("trailingPE"),
        "week_52_high": info.get("fiftyTwoWeekHigh"),
        "week_52_low": info.get("fiftyTwoWeekLow"),
        "currency": info.get("currency", "BRL"),
    }

    raw = t.history(period="2y")
    if not raw.empty:
        weekly = raw["Close"].resample("W").last().dropna()
        history = weekly.reset_index()
        history.columns = ["Date", "Close"]
    else:
        history = pd.DataFrame(columns=["Date", "Close"])

    return {"snapshot": snapshot, "history": history}


def format_market_context(data: dict, company_name: str, ticker: str) -> str:
    """Format yfinance data as a Portuguese text block for LLM prompt injection."""
    snap = data["snapshot"]
    history: pd.DataFrame = data["history"]

    lines = [f"=== Dados de Mercado: {company_name} ({ticker}) ==="]

    if snap.get("price") is not None:
        lines.append(f"Preço atual: R$ {snap['price']:,.2f}")
    if snap.get("market_cap") is not None:
        lines.append(f"Market Cap: R$ {snap['market_cap'] / 1e9:,.1f} bi")
    if snap.get("pe_ratio") is not None:
        lines.append(f"P/L: {snap['pe_ratio']:.1f}")
    if snap.get("week_52_high") is not None:
        lines.append(f"Máxima 52 semanas: R$ {snap['week_52_high']:,.2f}")
    if snap.get("week_52_low") is not None:
        lines.append(f"Mínima 52 semanas: R$ {snap['week_52_low']:,.2f}")

    if not history.empty:
        lines.append("")
        lines.append("Histórico de Preços (semanal, últimos 2 anos):")
        lines.append("Data          Fechamento")
        for _, row in history.iterrows():
            date_str = str(row["Date"])[:10]
            lines.append(f"{date_str}   R$ {row['Close']:,.2f}")

    return "\n".join(lines)
```

- [ ] **Step 4: Run the tests**

```bash
python3 test_market_data.py
```

Expected output (actual prices will differ):
```
=== test_market_data.py ===

✓ resolve_ticker
✓ fetch_market_data  price=38.42  history_rows=104
✓ format_market_context
=== Dados de Mercado: Petrobras (PETR4.SA) ===
Preço atual: R$ 38,42
Market Cap: R$ 497,3 bi
...

All tests passed.
```

- [ ] **Step 5: Commit**

```bash
git add app/scraper/market_data.py test_market_data.py
git commit -m "feat: add market_data module with yfinance ticker resolution and fetch"
```

---

## Task 3: Extend intent.py with "market" intent

**Files:**
- Modify: `app/query/intent.py` (full replacement, lines 1–24)

- [ ] **Step 1: Replace the full file content**

```python
from app.generation.llm import chat_completion

INTENT_SYSTEM_PROMPT = """You are an intent classifier for a document Q&A system about Brazilian listed companies.
Given a user message, determine the intent.

Reply with exactly one word:
- "market" if the user is asking about stock price, share price, market cap, price history, cotação, preço da ação, valor de mercado, histórico de preços, alta/baixa, máxima/mínima, 52 semanas, valorização, desvalorização, or how a stock is currently trading.
- "search" if the user is asking about financial statements, revenues, profits, EBITDA, balance sheets, debts, dividends, or other accounting/document-based financial data.
- "chat" if the user is making casual conversation, greeting, or asking something unrelated to financial data (e.g. "hello", "thanks", "how are you").

Reply with ONLY "market", "search", or "chat", nothing else."""


async def detect_intent(question: str) -> str:
    """Classify whether a question needs knowledge base search, market data, or is casual chat.

    Returns "search", "market", or "chat".
    """
    response = await chat_completion(INTENT_SYSTEM_PROMPT, question)
    intent = response.strip().lower()

    if intent not in ("search", "chat", "market"):
        return "search"

    return intent
```

- [ ] **Step 2: Quick smoke test**

```bash
python3 -c "
import asyncio
from app.query.intent import detect_intent

async def test():
    r1 = await detect_intent('Qual o preço atual da Petrobras?')
    r2 = await detect_intent('Qual o lucro líquido da Petrobras em 2023?')
    r3 = await detect_intent('Olá, tudo bem?')
    print(f'price query  → {r1!r}  (expected: market)')
    print(f'profit query → {r2!r}  (expected: search)')
    print(f'greeting     → {r3!r}  (expected: chat)')
    assert r1 == 'market'
    assert r2 == 'search'
    assert r3 == 'chat'
    print('All OK')

asyncio.run(test())
"
```

Expected: all three assertions pass.

- [ ] **Step 3: Commit**

```bash
git add app/query/intent.py
git commit -m "feat: add market intent to intent classifier"
```

---

## Task 4: Add build_market_prompt to prompts.py

**Files:**
- Modify: `app/generation/prompts.py` (append new function at end of file)

- [ ] **Step 1: Add the function**

Append to the end of `app/generation/prompts.py`:

```python

def build_market_prompt(question: str, market_text: str, alias_hint: str | None = None) -> str:
    """Build the user message for a market data query."""
    alias_block = f"Important: {alias_hint}\n\n" if alias_hint else ""
    return f"""{alias_block}Market data from Yahoo Finance:

{market_text}

---

Question: {question}"""
```

- [ ] **Step 2: Verify import**

```bash
python3 -c "from app.generation.prompts import build_market_prompt; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add app/generation/prompts.py
git commit -m "feat: add build_market_prompt to prompts"
```

---

## Task 5: Wire market branch into query.py

**Files:**
- Modify: `app/routers/query.py`

Two changes: update imports (lines 1–19), insert market branch in `run_pipeline()` (after the `intent == "chat"` block, before `"---- 2. Extract company ----"`).

- [ ] **Step 1: Update the import block at the top of the file**

In `app/routers/query.py`, replace the single existing prompts import line (line 15):
```python
from app.generation.prompts import build_system_prompt, build_rag_prompt
```
With these two lines:
```python
from app.generation.prompts import build_system_prompt, build_rag_prompt, build_market_prompt, RAG_SYSTEM_PROMPT
from app.scraper.market_data import resolve_ticker, fetch_market_data, format_market_context
```

- [ ] **Step 2: Insert the market branch in run_pipeline()**

In `run_pipeline()`, locate the block that ends with:
```python
            if intent == "chat":
                answer = await chat_completion(
                    GENERAL_SYSTEM_PROMPT, question, temperature=0.5
                )
                await queue.put({
                    "type": "answer",
                    "answer": answer,
                    "grounded": False,
                    "chunks": [],
                })
                return

            # ---- 2. Extract company and year from question ----
```

Insert the following block between `return` and the `# ---- 2.` comment:

```python
            if intent == "market":
                await emit("Identificando empresa na pergunta...")
                company_name = await extract_company(question)
                if not company_name:
                    await emit("Nenhuma empresa detectada. Respondendo com conhecimento geral.")
                    answer = await chat_completion(GENERAL_SYSTEM_PROMPT, question, temperature=0.5)
                    await queue.put({"type": "answer", "answer": answer, "grounded": False, "chunks": []})
                    return

                await emit(f"Empresa detectada: {company_name}")
                alias_hint = None
                if company_name.lower() not in question.lower():
                    alias_hint = (
                        f"The user is asking about '{company_name}'. "
                        f"Treat any alternative names as referring to the same company."
                    )

                ticker = resolve_ticker(company_name)
                if ticker is None:
                    await emit(
                        f"Ticker de {company_name} não encontrado na lista de empresas suportadas. "
                        "Respondendo com conhecimento geral."
                    )
                    answer = await chat_completion(GENERAL_SYSTEM_PROMPT, question, temperature=0.5)
                    await queue.put({"type": "answer", "answer": answer, "grounded": False, "chunks": []})
                    return

                await emit(f"Buscando dados de {company_name} ({ticker}) no Yahoo Finance...")
                try:
                    data = await asyncio.to_thread(fetch_market_data, ticker)
                except Exception as e:
                    await emit(f"Erro ao buscar dados de mercado: {e}. Respondendo com conhecimento geral.")
                    answer = await chat_completion(GENERAL_SYSTEM_PROMPT, question, temperature=0.5)
                    await queue.put({"type": "answer", "answer": answer, "grounded": False, "chunks": []})
                    return

                await emit("Gerando resposta...")
                market_text = format_market_context(data, company_name, ticker)
                user_message = build_market_prompt(question, market_text, alias_hint=alias_hint)
                answer = await chat_completion(RAG_SYSTEM_PROMPT, user_message, temperature=0.2)
                await queue.put({"type": "answer", "answer": answer, "grounded": True, "chunks": []})
                return

```

- [ ] **Step 3: Verify the server starts cleanly**

```bash
python3 -m uvicorn app.main:app --port 8001 --reload
```

Expected: `Application startup complete.` with no import errors.

- [ ] **Step 4: Test with curl**

In a second terminal (server must be running):
```bash
curl -N -s -X POST http://localhost:8001/query/stream \
  -H "Content-Type: application/json" \
  -d '{"question": "Qual o preço atual da Petrobras?"}' | head -20
```

Expected SSE stream: several `progress` events followed by an `answer` event where `"grounded": true`.

```bash
curl -N -s -X POST http://localhost:8001/query/stream \
  -H "Content-Type: application/json" \
  -d '{"question": "Qual o preço da Vivo hoje?"}' | head -20
```

Expected: alias hint triggers (`company_name = "Telefônica Brasil"`, `ticker = "VIVT3.SA"`), grounded answer returned.

```bash
curl -N -s -X POST http://localhost:8001/query/stream \
  -H "Content-Type: application/json" \
  -d '{"question": "Qual o lucro líquido da Petrobras em 2023?"}' | head -5
```

Expected: intent → `"search"`, pipeline goes through the normal CVM/vector-store path (not market branch).

- [ ] **Step 5: Commit**

```bash
git add app/routers/query.py
git commit -m "feat: wire market data branch into SSE query pipeline"
```

---

## Task 6: Final integration check

- [ ] **Step 1: Run test_market_data.py one more time**

```bash
python3 test_market_data.py
```

Expected: `All tests passed.`

- [ ] **Step 2: Manual browser test**

Start the server and open `http://localhost:8001` in a browser. Ask:
1. `"Qual o preço atual da Vale?"` → should show progress steps then a grounded market answer
2. `"Qual o market cap da Ambev?"` → grounded market answer
3. `"Histórico de preços do Itaú nos últimos 2 anos"` → grounded market answer with history
4. `"Qual o EBITDA da Petrobras em 2023?"` → should go through search/CVM path, NOT market path
5. `"Qual o preço de uma empresa que não existe?"` → intent=market, resolve_ticker=None → graceful fallback

- [ ] **Step 3: Final commit if any tweaks were needed**

```bash
git add -p   # stage only intentional changes
git commit -m "fix: <describe any tweaks found during integration test>"
```
