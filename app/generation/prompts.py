from app.generation.skills import build_system_prompt as _build_system_prompt

MARKET_SYSTEM_PROMPT = """You are a helpful assistant that answers questions about stock market data.
Answer ONLY based on the market data provided. Do not make up figures.
If a specific value is missing from the data, say so clearly. Be concise and direct."""

RAG_SYSTEM_PROMPT = """You are a helpful assistant that answers questions based on the provided document excerpts.

Rules:
- Answer ONLY based on the information in the provided context.
- If the context does not contain enough information to answer, say so clearly.
- Cite your sources by referencing the chunk numbers (e.g. [1], [2]).
- Be concise and direct.
- Do not make up information that is not in the context."""


def build_system_prompt(chunks: list[dict]) -> str:
    """Return RAG_SYSTEM_PROMPT with relevant skills appended based on chunk content."""
    return _build_system_prompt(RAG_SYSTEM_PROMPT, chunks)


def build_rag_prompt(question: str, chunks: list[dict], alias_hint: str | None = None) -> str:
    """Build the user message with retrieved chunks as context."""
    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        source = f"{chunk['filename']} (page {chunk['page_number']})"
        context_parts.append(f"[{i}] Source: {source}\n{chunk['text']}")

    context = "\n\n---\n\n".join(context_parts)

    alias_block = f"Important: {alias_hint}\n\n" if alias_hint else ""

    return f"""{alias_block}Context from the knowledge base:

{context}

---

Question: {question}"""


def build_market_prompt(question: str, market_text: str, alias_hint: str | None = None) -> str:
    """Build the user message for a market data query."""
    alias_block = f"Important: {alias_hint}\n\n" if alias_hint else ""
    return f"""{alias_block}Market data from Yahoo Finance:

{market_text}

---

Question: {question}"""
