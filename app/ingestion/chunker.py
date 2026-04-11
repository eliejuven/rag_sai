from app.config import CHUNK_SIZE, CHUNK_OVERLAP

SEPARATORS = ["\n\n", "\n", ". ", "! ", "? ", " "]


def _find_split_point(text: str, max_size: int) -> int:
    """Find the best position to split text at, trying each separator
    in order of preference (paragraph > line > sentence > word).
    Falls back to max_size if no separator is found.
    """
    for sep in SEPARATORS:
        pos = text.rfind(sep, 0, max_size)
        if pos != -1:
            return pos + len(sep)
    return max_size


def _build_page_index(pages: list[dict]) -> tuple[str, list[tuple[int, int]]]:
    """Combine page texts into a single string and build an index
    mapping character offsets to page numbers.

    Returns (full_text, [(start_offset, page_number), ...]).
    """
    parts = []
    page_index = []
    offset = 0

    for page in pages:
        text = page["text"]
        page_index.append((offset, page["page_number"]))
        parts.append(text)
        offset += len(text) + 1

    full_text = "\n".join(parts)
    return full_text, page_index


def _get_page_number(char_offset: int, page_index: list[tuple[int, int]]) -> int:
    """Given a character offset in the full text, return which page it belongs to."""
    page_num = page_index[0][1]
    for start_offset, pnum in page_index:
        if start_offset > char_offset:
            break
        page_num = pnum
    return page_num


def chunk_pages(
    pages: list[dict],
    chunk_size: int = CHUNK_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
) -> list[dict]:
    """Split extracted pages into overlapping chunks.

    Combines all pages into a single text, then splits using a hierarchy
    of separators (paragraph > line > sentence > word) to find natural
    break points. Each chunk overlaps with the next to preserve context
    at boundaries.

    Returns a list of dicts with keys: text, page_number, chunk_index.
    """
    full_text, page_index = _build_page_index(pages)

    chunks = []
    start = 0

    while start < len(full_text):
        end = min(start + chunk_size, len(full_text))

        if end < len(full_text):
            split_at = _find_split_point(full_text[start:end], chunk_size)
            end = start + split_at

        chunk_text = full_text[start:end].strip()
        if chunk_text:
            chunks.append(
                {
                    "text": chunk_text,
                    "page_number": _get_page_number(start, page_index),
                    "chunk_index": len(chunks),
                }
            )

        if end >= len(full_text):
            break

        start = max(end - chunk_overlap, start + 1)

    return chunks
