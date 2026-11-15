"""Audio helpers: WebM -> WAV, speech-to-text (faster-whisper), fluency, pace and tone."""
import io
import logging
import re
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np

from coach.config import MODELS_DIR, WHISPER_MODEL

log = logging.getLogger(__name__)

SILENCE_DBFS = -45.0
LONG_PAUSE_SEC = 2.0

# Whisper normally "cleans up" speech: it drops fillers and silently fixes grammar. Priming it
# with a disfluent, ungrammatical prompt makes it transcribe more literally, so filler words can
# be counted and learners' grammar mistakes are kept for correction.
_FILLER_PROMPT = ("Umm, so, uh, yesterday I go to school and, like, he don't know it. "
                  "I buyed two book and she have a car, you know.")
_FILLER_SINGLE = {"um", "umm", "uh", "uhh", "uhm", "er", "erm", "ah", "hmm", "mm", "like", "basically",
                  "actually", "literally", "so"}
_FILLER_PHRASES = ["you know", "i mean", "kind of", "sort of"]
# "like" / "so" / "actually" are only fillers some of the time; count them, but weigh them lower.
_SOFT_FILLERS = {"like", "so", "actually", "basically", "literally"}


def webm_to_wav(webm_bytes: bytes, out_path: Path) -> tuple[float, float]:
    """Convert browser WebM audio to 16 kHz mono WAV. Returns (duration_s, peak_dBFS)."""
    from pydub import AudioSegment

    audio = AudioSegment.from_file(io.BytesIO(webm_bytes), format="webm")
    audio = audio.set_channels(1).set_frame_rate(16000)
    audio.export(out_path, format="wav")
    peak = audio.max_dBFS if audio.max_dBFS != float("-inf") else -120.0
    return len(audio) / 1000.0, round(peak, 1)


@lru_cache(maxsize=1)
def _whisper():
    from faster_whisper import WhisperModel
    from faster_whisper.utils import download_model

    # Download into a plain folder: the default Hugging Face cache uses symlinks, which
    # Windows refuses without Developer Mode / admin rights.
    target = MODELS_DIR / f"faster-whisper-{WHISPER_MODEL}"
    if not (target / "model.bin").exists():
        log.info("Downloading faster-whisper model %s (one-time)…", WHISPER_MODEL)
        download_model(WHISPER_MODEL, output_dir=str(target))
    return WhisperModel(str(target), device="cpu", compute_type="int8")


_whisper_lock = threading.Lock()


def transcribe(wav_path: Path) -> dict:
    """Transcribe with word timestamps.

    Returns {"text", "words": [(word, start, end)], "speech_start", "speech_end"}.
    """
    import time

    model = _whisper()
    t0 = time.perf_counter()
    with _whisper_lock:  # one transcription at a time; CTranslate2 already uses all cores
        waited = time.perf_counter() - t0
        segments, _info = model.transcribe(
            str(wav_path), language="en", beam_size=1, word_timestamps=True,
            vad_filter=True, initial_prompt=_FILLER_PROMPT, condition_on_previous_text=False,
            # No temperature fallback: with the filler prompt, fallback retries made it ~7x slower
            # and caused repetition hallucinations.
            temperature=0.0,
        )
        # float(): Whisper returns numpy floats, which the LangGraph checkpointer can't serialize.
        words = [(w.word.strip(), float(w.start), float(w.end)) for seg in segments for w in (seg.words or [])]
    log.info("Whisper: %.1fs (waited %.1fs for lock) for %s, %d words",
             time.perf_counter() - t0, waited, wav_path.name, len(words))
    text = " ".join(w for w, _, _ in words).strip()
    return {
        "text": text,
        "words": words,
        "speech_start": words[0][1] if words else None,
        "speech_end": words[-1][2] if words else None,
    }


def fluency(words: list[tuple[str, float, float]], duration_sec: float) -> dict:
    """Filler words, long pauses and speaking pace computed from word timestamps."""
    if not words:
        return {"wpm": 0.0, "filler_count": 0, "fillers": {}, "filler_per_min": 0.0,
                "long_pauses": 0, "longest_pause": 0.0, "response_delay": None,
                "clarity_notes": "No words detected."}

    tokens = [re.sub(r"[^a-z']", "", w.lower()) for w, _, _ in words]
    fillers: dict[str, int] = {}
    for t in tokens:
        if t in _FILLER_SINGLE:
            fillers[t] = fillers.get(t, 0) + 1
    joined = " " + " ".join(tokens) + " "
    for p in _FILLER_PHRASES:
        n = joined.count(f" {p} ")
        if n:
            fillers[p] = n
    hard = sum(n for f, n in fillers.items() if f not in _SOFT_FILLERS)
    soft = sum(n for f, n in fillers.items() if f in _SOFT_FILLERS)
    filler_count = hard + soft

    gaps = [words[i + 1][1] - words[i][2] for i in range(len(words) - 1)]
    long_pauses = [g for g in gaps if g >= LONG_PAUSE_SEC]

    # Pace over the time actually spent speaking (first word -> last word).
    speaking = max(words[-1][2] - words[0][1], 1e-6)
    wpm = round(len(words) / (speaking / 60.0), 1)
    per_min = round((hard + 0.5 * soft) / (max(duration_sec, speaking) / 60.0), 1)

    notes = []
    if wpm < 100:
        notes.append("Pace is slow")
    elif wpm > 170:
        notes.append("Speaking too fast")
    else:
        notes.append("Good pace ✅")
    if per_min >= 6:
        notes.append(f"many filler words ({filler_count})")
    elif per_min >= 3:
        notes.append(f"some filler words ({filler_count})")
    if long_pauses:
        notes.append(f"{len(long_pauses)} long pause(s)")

    return {
        "wpm": wpm,
        "filler_count": filler_count,
        "fillers": dict(sorted(fillers.items(), key=lambda kv: -kv[1])),
        "filler_per_min": per_min,
        "long_pauses": len(long_pauses),
        "longest_pause": round(max(gaps), 1) if gaps else 0.0,
        "response_delay": round(words[0][1], 1),
        "clarity_notes": "; ".join(notes) + ".",
    }


def tone(wav_path: Path) -> str:
    """Pitch variance as a rough monotone check."""
    try:
        import librosa

        y, sr_rate = librosa.load(str(wav_path), sr=None)
        pitches, mags = librosa.piptrack(y=y, sr=sr_rate)
        pitch_values = pitches[mags > np.median(mags)]
        if pitch_values.size == 0:
            return "Not enough pitch data"
        return "Good vocal variety ✅" if float(np.var(pitch_values)) >= 100 else "Monotone tone"
    except Exception as e:
        log.warning("Tone analysis failed: %s", e)
        return "Tone analysis failed"
