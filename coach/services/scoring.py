"""Semantic similarity between a spoken answer and the ideal answers."""
from functools import lru_cache


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer("all-MiniLM-L6-v2")


def _chunks(text: str, size: int = 25) -> list[str]:
    """Sentences, with long sentences split into ~25-word windows."""
    import re

    out = []
    for sent in re.split(r"(?<=[.!?])\s+|\n+", text):
        words = sent.split()
        for i in range(0, len(words), size):
            if words[i:i + size]:
                out.append(" ".join(words[i:i + size]))
    return out


def key_point_coverage(answer: str, key_points: list[str], threshold: float = 0.55) -> tuple[float, float]:
    """(fraction of key points the answer covers, best single similarity).

    Each key point is compared with every sentence of the answer, so an answer that nails one
    point out of four scores 0.25 coverage, not 1.0.
    """
    from sentence_transformers import util

    chunks = _chunks((answer or "").strip())
    points = [p for p in key_points if p.strip()]
    if not chunks or not points:
        return 0.0, 0.0
    model = _model()
    sims = util.cos_sim(model.encode(points, convert_to_tensor=True),
                        model.encode(chunks, convert_to_tensor=True))  # points x chunks
    best_per_point = sims.max(dim=1).values.cpu().tolist()
    covered = sum(1 for s in best_per_point if s >= threshold)
    return covered / len(points), max(best_per_point)


def similarity_score(user_answer: str, ideal_answers: list[str]) -> tuple[float, str, list[str]]:
    """Returns (best_score 0..1, best_matching_ideal_answer, missing_keywords)."""
    from sentence_transformers import util

    user_answer = (user_answer or "").strip().lower()
    if not user_answer or not ideal_answers:
        return 0.0, "", []

    model = _model()
    user_emb = model.encode(user_answer, convert_to_tensor=True)
    ideal_embs = model.encode(ideal_answers, convert_to_tensor=True)
    sims = util.cos_sim(user_emb, ideal_embs)[0].cpu().tolist()

    best_idx = max(range(len(sims)), key=lambda i: sims[i])
    best_match = ideal_answers[best_idx]
    words = {w.strip(".,;:()").lower() for w in best_match.split() if len(w) > 3}
    missing = sorted(w for w in words if w and w not in user_answer)
    return max(0.0, float(sims[best_idx])), best_match, missing
