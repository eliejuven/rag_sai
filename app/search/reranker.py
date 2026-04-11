def reciprocal_rank_fusion(
    *result_lists: list[tuple[int, float]],
    k: int = 60,
    top_k: int = 5,
) -> list[tuple[int, float]]:
    """Merge multiple ranked result lists using Reciprocal Rank Fusion (RRF).

    RRF scores each result by: sum(1 / (k + rank)) across all lists
    where it appears. This balances results that rank high in different
    systems without needing to normalize their scores.

    Args:
        *result_lists: each is a list of (chunk_index, score) tuples.
        k: smoothing constant (default 60, standard in literature).
        top_k: number of results to return.

    Returns:
        list of (chunk_index, rrf_score) tuples, sorted by descending score.
    """
    rrf_scores: dict[int, float] = {}

    for result_list in result_lists:
        for rank, (chunk_index, _score) in enumerate(result_list):
            rrf_scores[chunk_index] = rrf_scores.get(chunk_index, 0.0) + 1.0 / (
                k + rank + 1
            )

    sorted_results = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    return sorted_results[:top_k]
