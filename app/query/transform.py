from app.generation.llm import chat_completion

TRANSFORM_SYSTEM_PROMPT = """You are a query rewriter for a document retrieval system.
Given a user question, rewrite it to improve retrieval from a knowledge base.

Guidelines:
- Expand abbreviations and acronyms.
- Make implicit context explicit.
- Keep the rewritten query concise (1-2 sentences max).
- Do NOT answer the question, only rewrite it.
- If the query is already clear and specific, return it unchanged.

Reply with ONLY the rewritten query, nothing else."""


async def transform_query(question: str) -> str:
    """Rewrite a user question to improve retrieval quality."""
    rewritten = await chat_completion(TRANSFORM_SYSTEM_PROMPT, question)
    return rewritten.strip()
