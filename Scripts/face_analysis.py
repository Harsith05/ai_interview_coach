# scripts/face_analysis.py
from deepface import DeepFace
import os

def analyze_frame(image_path):
    """
    Returns a concise result:
    { 'dominant_emotion': 'happy', 'emotion_scores': {..}, 'age': .., ... }
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(image_path)
    # analyze - returns list/dict
    res = DeepFace.analyze(img_path = image_path, actions = ['emotion'], enforce_detection=False)
    # res may be a dict
    # Simplify output
    out = {
        'dominant_emotion': res.get('dominant_emotion'),
        'emotion': res.get('emotion', {})
    }
    return out
