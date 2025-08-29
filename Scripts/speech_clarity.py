import librosa, numpy as np, re

def analyze_speech_rate(y, sr, transcript: str):
    """Words per minute based on transcript + audio duration"""
    words = len(re.findall(r"\w+", transcript))
    duration_sec = librosa.get_duration(y=y, sr=sr)
    if duration_sec == 0:
        return 0
    wpm = (words / duration_sec) * 60
    return round(wpm, 1)

def analyze_pitch(y, sr):
    """Mean + variation of pitch to estimate monotony/engagement"""
    pitches, magnitudes = librosa.piptrack(y=y, sr=sr)
    pitch_values = pitches[pitches > 0]
    if len(pitch_values) == 0:
        return None, None
    mean_pitch = float(np.mean(pitch_values))
    pitch_var = float(np.std(pitch_values))
    return round(mean_pitch, 1), round(pitch_var, 1)
