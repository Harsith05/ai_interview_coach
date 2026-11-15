"""Per-answer analysis subgraph (runs in the background, see coach.graph.jobs).

    prepare_audio -> transcribe -> score_content | analyze_delivery | analyze_body_language -> assemble

No LLM calls here: answers are graded together in one call at the end of the interview
(nodes.grade_answers) to stay within Gemini's small free-tier daily quota.
"""
import logging
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from coach.graph.state import AnswerState
from coach.services import audio, scoring, vision

log = logging.getLogger(__name__)


def prepare_audio(state: AnswerState) -> dict:
    sub = state.get("submission", {})
    webm = sub.get("webm_path")
    if not webm or not Path(webm).exists():
        return {"wav_path": None, "audio_duration": 0.0, "audio_peak_dbfs": None}
    wav = Path(webm).with_suffix(".wav")
    try:
        duration, peak = audio.webm_to_wav(Path(webm).read_bytes(), wav)
        return {"wav_path": str(wav), "audio_duration": duration, "audio_peak_dbfs": peak}
    except Exception:
        log.exception("Could not convert audio for turn %d", state["item"]["turn"])
        wav.unlink(missing_ok=True)
        return {"wav_path": None, "audio_duration": 0.0, "audio_peak_dbfs": None}


def transcribe(state: AnswerState) -> dict:
    sub = state.get("submission", {})
    live = (sub.get("live_transcript") or "").strip()
    path = state.get("wav_path")
    peak = state.get("audio_peak_dbfs")
    text, words = "", []

    if path:
        try:
            r = audio.transcribe(Path(path))
            text, words = r["text"], r["words"]
        except Exception as e:
            log.warning("Transcription failed: %s", e)

    if text:
        status = "Transcribed from recorded audio."
    elif live:
        text = live
        status = ("No recording was received; used the browser's live transcript." if not sub.get("audio_bytes")
                  else "Server could not recognize the recording; used the browser's live transcript.")
    elif not sub.get("audio_bytes"):
        status = "No audio was received from the browser (microphone not recording)."
    elif not path:
        status = "Audio was received but could not be decoded."
    elif peak is not None and peak < audio.SILENCE_DBFS:
        status = (f"Recording was silent (peak {peak} dBFS). The browser is probably using the wrong "
                  "microphone, or it is muted.")
    else:
        status = f"Audio was recorded (peak {peak} dBFS) but no speech was recognized."

    log.info("Turn %d transcript (%d chars): %s", state["item"]["turn"] + 1, len(text), status)
    return {"transcript": text, "words": words, "audio_status": status}


def score_content(state: AnswerState) -> dict:
    item = state["item"]
    refs = [item.get("ideal_answer", "")] + list(item.get("key_points", []))
    score, best, missing = scoring.similarity_score(state.get("transcript", ""), [r for r in refs if r])
    return {"content_eval": {"score": score, "best_match": best, "missing_keywords": missing}}


def analyze_delivery(state: AnswerState) -> dict:
    words = state.get("words") or []
    if words:
        f = audio.fluency([tuple(w) for w in words], state.get("audio_duration", 0.0))
    else:  # only the browser transcript is available: rough pace, no fluency data
        n = len(state.get("transcript", "").split())
        dur = state.get("audio_duration", 0.0)
        f = {"wpm": round(n / (dur / 60), 1) if dur and n else 0.0, "filler_count": 0, "fillers": {},
             "filler_per_min": 0.0, "long_pauses": 0, "longest_pause": 0.0, "response_delay": None,
             "clarity_notes": "No words detected." if not n else "Fluency not measured (no audio)."}
    path = state.get("wav_path")
    f["tone_notes"] = audio.tone(Path(path)) if path and words else "No audio"
    f["speech_rate"] = f["wpm"]
    return {"delivery_eval": f}


def analyze_body_language(state: AnswerState) -> dict:
    samples = state.get("submission", {}).get("camera_samples", []) or []
    return {"body_eval": vision.summarize_samples(samples)}


def assemble(state: AnswerState) -> dict:
    item = state["item"]
    return {"result": {
        "turn": item["turn"],
        "number": item.get("number"),
        "is_follow_up": item.get("is_follow_up", False),
        "difficulty": item.get("difficulty"),
        "topic": item.get("topic", ""),
        "q": item["question"],
        "key_points": item.get("key_points", []),
        "ideal": item.get("ideal_answer", ""),
        "transcript": state.get("transcript", ""),
        "audio_status": state.get("audio_status", ""),
        "grade": state.get("grade", {}),
        **state.get("content_eval", {}),
        **state.get("delivery_eval", {}),
        **state.get("body_eval", {}),
    }}


def build_analysis_graph():
    g = StateGraph(AnswerState)
    g.add_node("prepare_audio", prepare_audio)
    g.add_node("transcribe", transcribe)
    branches = {"score_content": score_content,
                "analyze_delivery": analyze_delivery, "analyze_body_language": analyze_body_language}
    for name, fn in branches.items():
        g.add_node(name, fn)
    g.add_node("assemble", assemble)

    g.add_edge(START, "prepare_audio")
    g.add_edge("prepare_audio", "transcribe")
    for name in branches:
        g.add_edge("transcribe", name)
    g.add_edge(list(branches), "assemble")
    g.add_edge("assemble", END)
    return g.compile()


analysis_graph = build_analysis_graph()


def fallback_result(item: dict, submission: dict, error: str) -> dict:
    return {"turn": item["turn"], "number": item.get("number"), "is_follow_up": item.get("is_follow_up", False),
            "difficulty": item.get("difficulty"), "topic": item.get("topic", ""), "q": item["question"],
            "key_points": item.get("key_points", []), "ideal": item.get("ideal_answer", ""),
            "transcript": submission.get("quick_transcript") or submission.get("live_transcript", ""),
            "audio_status": f"Analysis failed: {error}", "grade": {}, "score": 0.0}
