from app.generation.llm import chat_completion

INTENT_SYSTEM_PROMPT = """You are an intent classifier for a document Q&A system about Brazilian listed companies.
Given a user message, determine the intent.

Reply with exactly one word:
- "market" if the user is asking about stock price, share price, market cap, price history, cotação, preço da ação, valor de mercado, histórico de preços, alta/baixa, máxima/mínima, 52 semanas, valorização, desvalorização, or how a stock is currently trading.
- "search" if the user is asking about financial statements, revenues, profits, EBITDA, balance sheets, debts, dividends, or other accounting/document-based financial data.
- "chat" if the user is making casual conversation, greeting, or asking something unrelated to financial data (e.g. "hello", "thanks", "how are you").

Reply with ONLY "market", "search", or "chat", nothing else."""


async def detect_intent(question: str) -> str:
    """Classify whether a question needs knowledge base search, market data, or is casual chat.

    Returns "search", "market", or "chat".
    """
    response = await chat_completion(INTENT_SYSTEM_PROMPT, question)
    intent = response.strip().lower()

    if intent not in ("search", "chat", "market"):
        return "search"

    return intent
