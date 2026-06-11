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


async def main():
    print("=== test_bcb_client.py ===\n")
    test_cache()
    test_format_sections()
    print("\nOffline tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
