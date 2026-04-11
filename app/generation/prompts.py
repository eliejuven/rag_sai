RAG_SYSTEM_PROMPT = """You are a helpful assistant that answers questions based on the provided document excerpts.

Rules:
- Answer ONLY based on the information in the provided context.
- If the context does not contain enough information to answer, say so clearly.
- Cite your sources by referencing the chunk numbers (e.g. [1], [2]).
- Be concise and direct.
- Do not make up information that is not in the context."""


def build_rag_prompt(question: str, chunks: list[dict]) -> str:
    """Build the user message with retrieved chunks as context."""
    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        source = f"{chunk['filename']} (page {chunk['page_number']})"
        context_parts.append(f"[{i}] Source: {source}\n{chunk['text']}")

    context = "\n\n---\n\n".join(context_parts)

    return f"""Context from the knowledge base:

{context}

---

Question: {question}"""
