# scripts/nlp_scoring.py
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

def similarity_score(user_answer: str, ideal_answer: str) -> float:
    """
    TF-IDF cosine similarity between user answer and ideal answer.
    Returns float between 0.0 and 1.0
    """
    user_answer = (user_answer or "").strip().lower()
    ideal_answer = (ideal_answer or "").strip().lower()
    if not user_answer or not ideal_answer:
        return 0.0

    vect = TfidfVectorizer(stop_words='english')
    X = vect.fit_transform([user_answer, ideal_answer])
    sim = cosine_similarity(X[0:1], X[1:2])[0, 0]
    return float(max(0.0, min(1.0, sim)))
