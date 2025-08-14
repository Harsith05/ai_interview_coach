# scripts/nlp_scoring.py
from sentence_transformers import SentenceTransformer, util
import numpy as np

# load model once
MODEL = SentenceTransformer('all-MiniLM-L6-v2')  # lightweight & fast

def compute_similarity(user_text: str, ideal_text: str) -> float:
    if not user_text or not ideal_text:
        return 0.0
    # encode both
    emb1 = MODEL.encode(user_text, convert_to_tensor=True)
    emb2 = MODEL.encode(ideal_text, convert_to_tensor=True)
    cos = util.cos_sim(emb1, emb2).item()
    # clamp to 0..1
    cos = max(0.0, min(1.0, cos))
    return float(cos)
