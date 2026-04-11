import numpy as np


class VectorStore:
    """In-memory vector store using numpy for cosine similarity search.

    Stores embedding vectors in a numpy matrix and performs brute-force
    cosine similarity search. Simple and effective for small-to-medium
    knowledge bases (up to ~100k chunks).
    """

    def __init__(self):
        self._vectors: np.ndarray | None = None
        self._chunk_indices: list[int] = []

    @property
    def size(self) -> int:
        return len(self._chunk_indices)

    def add(self, vectors: list[list[float]], chunk_indices: list[int]) -> None:
        """Add vectors to the store, mapped to their chunk indices in storage.chunks."""
        new_matrix = np.array(vectors, dtype=np.float32)

        norms = np.linalg.norm(new_matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        new_matrix = new_matrix / norms

        if self._vectors is None:
            self._vectors = new_matrix
        else:
            self._vectors = np.vstack([self._vectors, new_matrix])

        self._chunk_indices.extend(chunk_indices)

    def search(self, query_vector: list[float], top_k: int = 5) -> list[tuple[int, float]]:
        """Find the top-k most similar chunks to the query vector.

        Returns a list of (chunk_index, similarity_score) tuples,
        sorted by descending similarity.
        """
        if self._vectors is None or self.size == 0:
            return []

        query = np.array(query_vector, dtype=np.float32)
        query = query / (np.linalg.norm(query) or 1.0)

        similarities = self._vectors @ query

        top_k = min(top_k, self.size)
        top_indices = np.argpartition(similarities, -top_k)[-top_k:]
        top_indices = top_indices[np.argsort(similarities[top_indices])[::-1]]

        return [
            (self._chunk_indices[i], float(similarities[i]))
            for i in top_indices
        ]


vector_store = VectorStore()
