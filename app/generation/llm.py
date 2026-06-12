import asyncio

import httpx

from app.config import MISTRAL_API_KEY, MISTRAL_API_BASE, MISTRAL_CHAT_MODEL

CHAT_URL = f"{MISTRAL_API_BASE}/chat/completions"
HEADERS = {
    "Authorization": f"Bearer {MISTRAL_API_KEY}",
    "Content-Type": "application/json",
}

# Mistral's free/low tiers return 429 quickly under concurrent load (e.g.
# Phase 1's per-FRE-section extraction calls) or for large single requests
# (e.g. Phase 4's ~20k-token Dossier-context prompts, which can saturate a
# per-minute token quota on their own). Retry with backoff rather than
# silently dropping the section's content — 6 retries gives up to ~2 minutes
# of backoff, comfortably past a per-minute quota reset.
_MAX_RETRIES = 6
_BACKOFF_SECONDS = 2.0


async def chat_completion(
    system_prompt: str,
    user_message: str,
    temperature: float = 0.0,
    json_mode: bool = False,
) -> str:
    """Send a chat completion request to Mistral and return the response text."""
    payload = {
        "model": MISTRAL_CHAT_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in range(_MAX_RETRIES + 1):
            response = await client.post(CHAT_URL, json=payload, headers=HEADERS)
            if response.status_code == 429 and attempt < _MAX_RETRIES:
                await asyncio.sleep(_BACKOFF_SECONDS * (2 ** attempt))
                continue
            response.raise_for_status()
            break

    data = response.json()
    return data["choices"][0]["message"]["content"]
