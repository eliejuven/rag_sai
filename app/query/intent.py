from app.generation.llm import chat_completion

INTENT_SYSTEM_PROMPT = """You are an intent classifier for a document Q&A system.
Given a user message, determine if it requires searching a knowledge base.

Reply with exactly one word:
- "search" if the user is asking a question that requires looking up information in documents.
- "chat" if the user is making casual conversation, greeting, or asking something that does not require document search (e.g. "hello", "thanks", "how are you").

Reply with ONLY "search" or "chat", nothing else."""


async def detect_intent(question: str) -> str:
    """Classify whether a question needs knowledge base search.

    Returns "search" or "chat".
    """
    response = await chat_completion(INTENT_SYSTEM_PROMPT, question)
    intent = response.strip().lower()

    if intent not in ("search", "chat"):
        return "search"

    return intent
