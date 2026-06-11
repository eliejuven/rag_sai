"""Standalone test script for app/scraper/bcb_client.py"""
import asyncio
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path


def test_cache():
    import app.scraper.bcb_client as bcb

    original_path = bcb.CACHE_PATH
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = Path(f.name)

    try:
        bcb.CACHE_PATH = tmp_path
        tmp_path.unlink()  # start with no file

        assert bcb._load_cache() is None, "Expected None for missing cache"

        mock_data = {
            "series": {"selic": [{"data": "01/06/2026", "valor": "10.50"}]},
            "focus": {},
        }
        bcb._save_cache(mock_data)

        cache = bcb._load_cache()
        assert cache is not None
        assert "fetched_at" in cache
        assert cache["data"] == mock_data
        assert not bcb._is_stale(cache), "Fresh cache should not be stale"

        # Backdate the timestamp → should be stale
        old_payload = {
            "fetched_at": (datetime.now() - timedelta(days=400)).isoformat(),
            "data": mock_data,
        }
        tmp_path.write_text(json.dumps(old_payload))
        assert bcb._is_stale(bcb._load_cache()), "400-day-old cache should be stale"

        print("✓ test_cache")
    finally:
        bcb.CACHE_PATH = original_path
        if tmp_path.exists():
            tmp_path.unlink()


def test_format_sections():
    from app.scraper.bcb_client import format_macro_sections

    mock_data = {
        "series": {
            "selic":        [{"data": "01/06/2026", "valor": "10.50"}],
            "ipca":         [{"data": "01/06/2026", "valor": "5.06"}],
            "brl_usd":      [{"data": "01/06/2026", "valor": "5.73"}],
            "igpm":         [{"data": "01/06/2026", "valor": "4.12"}],
            "unemployment": [{"data": "01/06/2026", "valor": "6.20"}],
            "gdp":          [{"data": "01/06/2026", "valor": "2.90"}],
        },
        "focus": {
            "ipca":    {"valor": 4.5,  "data": "2026-06-10", "ano": 2026},
            "selic":   {"valor": 10.5, "data": "2026-06-10", "ano": 2026},
            "brl_usd": {"valor": 5.8,  "data": "2026-06-10", "ano": 2026},
            "pib":     {"valor": 2.1,  "data": "2026-06-10", "ano": 2026},
        },
    }

    sections = format_macro_sections(mock_data)
    assert len(sections) == 5, f"Expected 5 sections, got {len(sections)}"
    assert sections[0]["section"] == "Política Monetária"
    assert sections[1]["section"] == "Inflação"
    assert sections[2]["section"] == "Câmbio"
    assert sections[3]["section"] == "Atividade Econômica"
    assert sections[4]["section"] == "Focus Report"
    assert "10.50" in sections[0]["text"]
    assert "5.06" in sections[1]["text"]
    assert "4.12" in sections[1]["text"]   # IGPM in Inflação section
    assert "5.73" in sections[2]["text"]
    assert "2.90" in sections[3]["text"]   # GDP
    assert "6.20" in sections[3]["text"]   # unemployment
    assert "4.5"  in sections[4]["text"]   # Focus IPCA mediana
    print("✓ test_format_sections")


async def test_fetch_series():
    from app.scraper.bcb_client import _fetch_series
    import httpx

    async with httpx.AsyncClient(timeout=15.0) as client:
        readings = await _fetch_series(client, "selic")
    assert isinstance(readings, list), f"Expected list, got {type(readings)}"
    assert 1 <= len(readings) <= 12, f"Expected 1-12 readings, got {len(readings)}"
    for r in readings:
        assert "data" in r, f"Missing 'data' key in reading: {r}"
        assert "valor" in r, f"Missing 'valor' key in reading: {r}"
    print(f"✓ test_fetch_series  latest={readings[-1]}")


async def test_fetch_focus():
    import httpx
    from app.scraper.bcb_client import _fetch_focus

    async with httpx.AsyncClient(timeout=15.0) as client:
        result = await _fetch_focus(client, "ipca")
    assert result is not None, "Expected Focus data for IPCA, got None"
    assert "valor" in result, f"Missing 'valor': {result}"
    assert "data"  in result, f"Missing 'data': {result}"
    assert "ano"   in result, f"Missing 'ano': {result}"
    print(f"✓ test_fetch_focus  ipca={result}")


async def test_snapshot_line():
    from app.scraper.bcb_client import get_macro_snapshot_line

    line = await get_macro_snapshot_line()
    if line == "Dados BCB indisponíveis.":
        print(f"✓ test_snapshot_line  (BCB unavailable, fallback returned)")
        return
    known_labels = ["Selic", "IPCA", "BRL/USD", "IGPM", "Desemprego", "PIB"]
    found = [label for label in known_labels if label in line]
    assert len(found) >= 1, f"Expected at least one indicator label in snapshot line: {line}"
    assert "|" in line, "Expected pipe-separated values"
    print(f"✓ test_snapshot_line  {line}")


async def main():
    print("=== test_bcb_client.py ===\n")
    test_cache()
    test_format_sections()
    await test_fetch_series()
    await test_fetch_focus()
    await test_snapshot_line()
    print("\nAll tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
