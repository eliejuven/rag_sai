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

        # Valuation
        "forward_pe": info.get("forwardPE"),
        "price_to_book": info.get("priceToBook"),
        "price_to_sales": info.get("priceToSalesTrailing12Months"),
        "enterprise_value": info.get("enterpriseValue"),
        "ev_to_ebitda": info.get("enterpriseToEbitda"),
        "peg_ratio": info.get("trailingPegRatio") or info.get("pegRatio"),

        # Dividend
        "dividend_rate": info.get("dividendRate"),

        # Profitability / health
        "ebitda_margin": info.get("ebitdaMargins"),
        "roe": info.get("returnOnEquity"),
        "roa": info.get("returnOnAssets"),
        "profit_margin": info.get("profitMargins"),
        "operating_margin": info.get("operatingMargins"),
        "debt_to_equity": info.get("debtToEquity"),
        "current_ratio": info.get("currentRatio"),
        "total_cash": info.get("totalCash"),
        "total_debt": info.get("totalDebt"),
        "free_cashflow": info.get("freeCashflow"),
        "revenue_growth": info.get("revenueGrowth"),
        "earnings_growth": info.get("earningsGrowth"),

        # Analyst
        "recommendation": info.get("recommendationKey"),

        # Profile
        "employees": info.get("fullTimeEmployees"),
        "business_summary": info.get("longBusinessSummary"),
    }

    raw = t.history(period="5y", interval="1mo")
    if not raw.empty:
        monthly = raw["Close"].dropna()
        history = monthly.reset_index()
        history.columns = ["Date", "Close"]
    else:
        history = pd.DataFrame(columns=["Date", "Close"])

    return {"snapshot": snapshot, "history": history}


def format_market_sections(data: dict, company_name: str, ticker: str) -> list[dict]:
    """
    Split market data into labeled sections for citation.
    Returns list of {"section": name, "text": content}.
    Sections map to [1], [2], [3] in the LLM prompt.
    """
    snap = data["snapshot"]
    history: pd.DataFrame = data["history"]
    header = f"{company_name} ({ticker})"
    sections = []

    # --- Section 1: Cotação e Valuation ---
    s1 = [f"=== {header} — Cotação e Valuation ==="]
    if snap.get("price") is not None:
        s1.append(f"Preço atual: R$ {snap['price']:,.2f}")
    if snap.get("market_cap") is not None:
        s1.append(f"Market Cap: R$ {snap['market_cap'] / 1e9:,.1f} bi")
    if snap.get("pe_ratio") is not None:
        s1.append(f"P/L: {snap['pe_ratio']:.1f}")
    if snap.get("week_52_high") is not None:
        s1.append(f"Máxima 52 semanas: R$ {snap['week_52_high']:,.2f}")
    if snap.get("week_52_low") is not None:
        s1.append(f"Mínima 52 semanas: R$ {snap['week_52_low']:,.2f}")
    val_lines = []
    if snap.get("forward_pe") is not None:
        val_lines.append(f"P/L projetado: {snap['forward_pe']:.1f}")
    if snap.get("price_to_book") is not None:
        val_lines.append(f"P/VP: {snap['price_to_book']:.2f}")
    if snap.get("price_to_sales") is not None:
        val_lines.append(f"P/Receita: {snap['price_to_sales']:.2f}")
    if snap.get("enterprise_value") is not None:
        val_lines.append(f"Enterprise Value: R$ {snap['enterprise_value'] / 1e9:,.1f} bi")
    if snap.get("ev_to_ebitda") is not None:
        val_lines.append(f"EV/EBITDA: {snap['ev_to_ebitda']:.1f}")
    if snap.get("peg_ratio") is not None:
        val_lines.append(f"PEG: {snap['peg_ratio']:.2f}")
    if val_lines:
        s1.append("")
        s1.append("Valuation:")
        s1.extend(val_lines)
    if snap.get("dividend_rate") is not None:
        s1.append(f"Dividendo por ação (anual): R$ {snap['dividend_rate']:,.2f}")
    if snap.get("recommendation") is not None:
        s1.append(f"Recomendação dos analistas: {snap['recommendation']}")
    if len(s1) > 1:
        sections.append({"section": "Cotação e Valuation", "text": "\n".join(s1)})

    # --- Section 2: Rentabilidade e Saúde Financeira ---
    s2 = [f"=== {header} — Rentabilidade e Saúde Financeira ==="]
    if snap.get("ebitda_margin") is not None:
        s2.append(f"Margem EBITDA: {snap['ebitda_margin'] * 100:.1f}%")
    if snap.get("roe") is not None:
        s2.append(f"ROE: {snap['roe'] * 100:.1f}%")
    if snap.get("roa") is not None:
        s2.append(f"ROA: {snap['roa'] * 100:.1f}%")
    if snap.get("profit_margin") is not None:
        s2.append(f"Margem líquida: {snap['profit_margin'] * 100:.1f}%")
    if snap.get("operating_margin") is not None:
        s2.append(f"Margem operacional: {snap['operating_margin'] * 100:.1f}%")
    if snap.get("debt_to_equity") is not None:
        s2.append(f"Dívida/Patrimônio (D/E): {snap['debt_to_equity']:.1f}")
    if snap.get("current_ratio") is not None:
        s2.append(f"Liquidez corrente: {snap['current_ratio']:.2f}")
    if snap.get("total_cash") is not None:
        s2.append(f"Caixa total: R$ {snap['total_cash'] / 1e9:,.1f} bi")
    if snap.get("total_debt") is not None:
        s2.append(f"Dívida total: R$ {snap['total_debt'] / 1e9:,.1f} bi")
    if snap.get("free_cashflow") is not None:
        s2.append(f"Fluxo de caixa livre: R$ {snap['free_cashflow'] / 1e9:,.1f} bi")
    if snap.get("revenue_growth") is not None:
        s2.append(f"Crescimento da receita: {snap['revenue_growth'] * 100:.1f}%")
    if snap.get("earnings_growth") is not None:
        s2.append(f"Crescimento dos lucros: {snap['earnings_growth'] * 100:.1f}%")
    if snap.get("employees") is not None:
        s2.append(f"Funcionários: {snap['employees']:,}")
    if snap.get("business_summary") is not None:
        s2.append("")
        s2.append("Resumo da empresa:")
        s2.append(snap["business_summary"])
    if len(s2) > 1:
        sections.append({"section": "Rentabilidade e Saúde Financeira", "text": "\n".join(s2)})
        
# --- Section 3: Histórico de Preços ---
    if not history.empty:
        s3 = [f"=== {header} — Histórico de Preços (mensal, últimos 5 anos) ==="]
        s3.append("Data           Fechamento")
        for _, row in history.iterrows():
            date_str = str(row["Date"])[:10]
            s3.append(f"{date_str}   R$ {row['Close']:,.2f}")
        sections.append({"section": "Histórico de Preços", "text": "\n".join(s3)})

    if not sections:
        sections.append({
            "section": "Dados de Mercado",
            "text": f"Dados de mercado indisponíveis para {ticker}.",
        })

    return sections


def format_market_context(data: dict, company_name: str, ticker: str) -> str:
    """Format yfinance data as a single text block (joins all sections)."""
    return "\n\n".join(s["text"] for s in format_market_sections(data, company_name, ticker))