# scripts/nlp_scoring.py
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

def similarity_score(user_answer: str, ideal_answers: list[str]):
    """
    Compare user answer against multiple ideal answers.
    Returns: (best_score, best_match, missing_keywords)
    """
    user_answer = (user_answer or "").strip().lower()
    if not user_answer or not ideal_answers:
        return 0.0, "", []

    best_score = 0.0
    best_match = ""
    missing_keywords = []

    for ideal in ideal_answers:
        vect = TfidfVectorizer(stop_words='english')
        X = vect.fit_transform([user_answer, ideal.lower()])
        sim = cosine_similarity(X[0:1], X[1:2])[0, 0]
        if sim > best_score:
            best_score = sim
            best_match = ideal

            # keyword check
            words = set(ideal.lower().split())
            missing_keywords = [w for w in words if w not in user_answer]

    return float(best_score), best_match, missing_keywords