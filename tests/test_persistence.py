"""Test save/load persistence cycle."""
import asyncio
import json
from pathlib import Path
from app.scraper.pipeline import scrape_and_ingest, METADATA_PATH
from app.search.vector_store import vector_store
from app import storage


async def main():
    # Force fresh scrape by removing Petrobras from metadata
    if METADATA_PATH.exists():
        meta = json.loads(METADATA_PATH.read_text())
        meta.pop("33.000.167/0001-01", None)
        METADATA_PATH.write_text(json.dumps(meta))

    # Step 1: ingest Petrobras data
    print("=== Step 1: Scrape and save ===")
    await scrape_and_ingest("Petrobras")
    print(f"After scrape — chunks: {len(storage.chunks)}, vectors: {vector_store.size}")

    # Step 2: simulate server restart by clearing all in-memory state
    print("\n=== Step 2: Simulate restart (clear memory) ===")
    storage.chunks.clear()
    storage.documents.clear()
    vector_store._vectors = None
    vector_store._chunk_indices = []
    print(f"After clear — chunks: {len(storage.chunks)}, vectors: {vector_store.size}")

    # Step 3: load persisted state
    print("\n=== Step 3: Load from disk ===")
    from app.persistence import load_state
    loaded = load_state()
    print(f"load_state() returned: {loaded}")
    print(f"After load  — chunks: {len(storage.chunks)}, vectors: {vector_store.size}")

    # Step 4: verify a chunk looks right
    if storage.chunks:
        c = storage.chunks[0]
        print(f"\nFirst chunk source:   {c.get('source')}")
        print(f"First chunk filename: {c.get('filename')}")
        print(f"First chunk text:\n{c['text'][:200]}")


asyncio.run(main())
