import math
import re


class BM25Index:
    """BM25 keyword search index.

    BM25 (Best Matching 25) is a ranking function that scores documents
    based on term frequency (how often a word appears in a chunk) and
    inverse document frequency (how rare the word is across all chunks).

    Rare words that appear frequently in a chunk produce high scores.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self._k1 = k1
        self._b = b
        self._chunk_indices: list[int] = []
        self._doc_lengths: list[int] = []
        self._avg_doc_length: float = 0.0
        self._token_freqs: list[dict[str, int]] = []
        self._doc_freq: dict[str, int] = {}
        self._total_docs: int = 0

    @property
    def size(self) -> int:
        return self._total_docs

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Lowercase and split into alphanumeric tokens."""
        return re.findall(r"[a-z0-9]+", text.lower())

    def add(self, texts: list[str], chunk_indices: list[int]) -> None:
        """Index a batch of chunk texts for keyword search."""
        for text, idx in zip(texts, chunk_indices):
            tokens = self._tokenize(text)

            freq: dict[str, int] = {}
            for token in tokens:
                freq[token] = freq.get(token, 0) + 1

            self._token_freqs.append(freq)
            self._doc_lengths.append(len(tokens))
            self._chunk_indices.append(idx)

            for token in freq:
                self._doc_freq[token] = self._doc_freq.get(token, 0) + 1

        self._total_docs = len(self._chunk_indices)
        self._avg_doc_length = (
            sum(self._doc_lengths) / self._total_docs if self._total_docs else 0.0
        )

    def search(self, query: str, top_k: int = 5) -> list[tuple[int, float]]:
        """Search for chunks matching the query terms.

        Returns a list of (chunk_index, bm25_score) tuples,
        sorted by descending score.
        """
        if self._total_docs == 0:
            return []

        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scores: list[float] = [0.0] * self._total_docs

        for token in query_tokens:
            if token not in self._doc_freq:
                continue

            df = self._doc_freq[token]
            idf = math.log((self._total_docs - df + 0.5) / (df + 0.5) + 1.0)

            for i, freq in enumerate(self._token_freqs):
                if token not in freq:
                    continue
                tf = freq[token]
                doc_len = self._doc_lengths[i]
                numerator = tf * (self._k1 + 1)
                denominator = tf + self._k1 * (
                    1 - self._b + self._b * doc_len / self._avg_doc_length
                )
                scores[i] += idf * numerator / denominator

        scored = [
            (self._chunk_indices[i], scores[i])
            for i in range(self._total_docs)
            if scores[i] > 0
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


bm25_index = BM25Index()
