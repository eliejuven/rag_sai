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
    Fetch snapshot + 2-year weekly history from Yahoo Finance.
    Synchronous — call via asyncio.to_thread(fetch_market_data, ticker) from async code.
    """
    t = yf.Ticker(ticker)
    info = t.info

    snapshot = {
        "price": info.get("currentPrice") if info.get("currentPrice") is not None else info.get("regularMarketPrice"),
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
        lines.append("Data           Fechamento")
        for _, row in history.iterrows():
            date_str = str(row["Date"])[:10]
            lines.append(f"{date_str}   R$ {row['Close']:,.2f}")
    elif all(snap.get(k) is None for k in ("price", "market_cap", "pe_ratio", "week_52_high", "week_52_low")):
        lines.append("Dados de mercado indisponíveis para este ticker.")

    return "\n".join(lines)
