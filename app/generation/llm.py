import httpx

from app.config import MISTRAL_API_KEY, MISTRAL_API_BASE, MISTRAL_CHAT_MODEL

CHAT_URL = f"{MISTRAL_API_BASE}/chat/completions"
HEADERS = {
    "Authorization": f"Bearer {MISTRAL_API_KEY}",
    "Content-Type": "application/json",
}


async def chat_completion(
    system_prompt: str,
    user_message: str,
    temperature: float = 0.0,
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

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(CHAT_URL, json=payload, headers=HEADERS)
        response.raise_for_status()

    data = response.json()
    return data["choices"][0]["message"]["content"]
