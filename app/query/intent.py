from app.generation.llm import chat_completion

INTENT_SYSTEM_PROMPT = """You are an intent classifier for a document Q&A system about Brazilian listed companies.
Given a user message, determine the intent.

Reply with exactly one word:
- "macro" if the user is asking about macroeconomic indicators: Selic, taxa de juros, IPCA, inflação, câmbio, BRL/USD, dólar, IGPM, desemprego, PIB, crescimento econômico, or Focus Report forecasts (projeção, expectativa, previsão) for any of these. Even when no specific company is named. Questions that mix a specific company with macro indicators (e.g. "como a Selic afeta a Petrobras?") are NOT "macro" — they are "search".
- "market" if the user is asking about: stock price, current price, share price, market cap (capitalização de mercado), price history, cotação, preço atual, preço da ação, valor de mercado, histórico de preços, alta/baixa, máxima/mínima, 52 semanas, valorização, desvalorização, or how a stock is currently trading. Even if the company is unknown or fictitious, questions about price/cotação/market cap are always "market". Market cap and current valuation ARE market questions, not accounting questions.
- "search" if the user is asking about financial statements, revenues (receita), profits (lucro), EBITDA, balance sheets (balanço), debts (dívida), dividends (dividendos), or other accounting/document-based data from regulatory filings. Also use "search" for questions mixing a specific company with macro context.
- "chat" if the user is making casual conversation, greeting, or asking something completely unrelated to financial data (e.g. "hello", "thanks", "how are you").

Examples:
- "Qual a Selic atual?" → macro
- "Como está a inflação no Brasil?" → macro
- "Qual a previsão do IPCA para 2025?" → macro
- "Qual o câmbio hoje?" → macro
- "Como a Selic afeta os lucros do Itaú?" → search
- "Qual o preço atual da Vale?" → market
- "Qual o market cap da Ambev?" → market
- "Qual é a capitalização de mercado da Petrobras?" → market
- "Histórico de preços do Itaú nos últimos 2 anos" → market
- "Qual o preço da Empresa Fictícia XYZ?" → market
- "Qual o EBITDA da Petrobras em 2023?" → search
- "Qual a receita da Magazine Luiza?" → search
- "Olá, tudo bem?" → chat

Reply with ONLY "macro", "market", "search", or "chat", nothing else."""


async def detect_intent(question: str) -> str:
    """Classify question intent. Returns "macro", "search", "market", or "chat"."""
    response = await chat_completion(INTENT_SYSTEM_PROMPT, question)
    intent = response.strip().lower()

    if intent not in ("search", "chat", "market", "macro"):
        return "search"

    return intent
