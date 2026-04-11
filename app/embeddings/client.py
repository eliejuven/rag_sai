import httpx

from app.config import MISTRAL_API_KEY, MISTRAL_API_BASE, MISTRAL_EMBED_MODEL

EMBED_URL = f"{MISTRAL_API_BASE}/embeddings"
HEADERS = {
    "Authorization": f"Bearer {MISTRAL_API_KEY}",
    "Content-Type": "application/json",
}

# Mistral's embedding API accepts up to 16 texts per request
BATCH_SIZE = 16


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Convert a list of texts into embedding vectors using Mistral's API.

    Handles batching automatically for lists longer than 16 texts.
    Returns a list of vectors (each vector is a list of floats).
    """
    all_embeddings: list[list[float]] = []

    async with httpx.AsyncClient(timeout=60.0) as client:
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i : i + BATCH_SIZE]
            payload = {
                "model": MISTRAL_EMBED_MODEL,
                "input": batch,
            }

            response = await client.post(EMBED_URL, json=payload, headers=HEADERS)
            response.raise_for_status()

            data = response.json()
            batch_embeddings = [item["embedding"] for item in data["data"]]
            all_embeddings.extend(batch_embeddings)

    return all_embeddings
