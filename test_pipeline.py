"""End-to-end test for scraper pipeline."""
import asyncio
from app.scraper.pipeline import scrape_and_ingest
from app import storage
from app.search.vector_store import vector_store


async def main():
    print("=== Test 1: First scrape (Petrobras) ===\n")

    async def progress(msg: str):
        print(f"  [progress] {msg}")

    result = await scrape_and_ingest("Petrobras", progress=progress)

    print(f"\nResult: {result}")
    print(f"Vector store size: {vector_store.size}")
    print(f"Total chunks in storage: {len(storage.chunks)}")

    if storage.chunks:
        print(f"\nSample chunk filename: {storage.chunks[0]['filename']}")
        print(f"Sample chunk source:   {storage.chunks[0]['source']}")
        print(f"Sample chunk text (first 200 chars):\n{storage.chunks[0]['text'][:200]}")

    print("\n=== Test 2: Second scrape (same company — should use cache) ===\n")
    result2 = await scrape_and_ingest("Petrobras", progress=progress)
    print(f"\nResult: {result2}")
    print(f"scraped=False means cache was used: {not result2['scraped']}")


asyncio.run(main())
