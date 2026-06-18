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
