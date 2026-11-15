"""Camera frame analysis (gaze, posture, emotion) and the server-side webcam stream."""
import base64
import logging
import threading
from collections import Counter
from functools import lru_cache

import cv2
import numpy as np

log = logging.getLogger(__name__)
_mesh_lock = threading.Lock()


@lru_cache(maxsize=1)
def _face_mesh():
    import mediapipe as mp

    return mp.solutions.face_mesh.FaceMesh(
        static_image_mode=False, max_num_faces=1, refine_landmarks=True
    )


@lru_cache(maxsize=1)
def _face_cascade():
    return cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


def decode_data_url(data_url: str) -> np.ndarray | None:
    try:
        b64 = data_url.split(",", 1)[1] if "," in data_url else data_url
        arr = np.frombuffer(base64.b64decode(b64), np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception:
        return None


def analyze_frame(frame: np.ndarray) -> dict:
    gaze, posture = "Unknown", "Unknown"

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    with _mesh_lock:  # mediapipe graphs are not thread-safe
        results = _face_mesh().process(rgb)
    if results.multi_face_landmarks:
        lm = results.multi_face_landmarks[0].landmark
        left_iris, eye_l, eye_r = lm[468], lm[33], lm[133]
        iris_pos = (left_iris.x - eye_l.x) / ((eye_r.x - eye_l.x) + 1e-6)
        gaze = "Looking Left" if iris_pos < 0.35 else "Looking Right" if iris_pos > 0.65 else "Forward"
        posture = "Straight" if lm[152].y - lm[1].y > 0.1 else "Slouched"

    emotion = "Unknown"
    try:
        from deepface import DeepFace

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = _face_cascade().detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(50, 50))
        if len(faces) > 0:
            x, y, w, h = faces[0]
            analysis = DeepFace.analyze(frame[y:y + h, x:x + w], actions=["emotion"], enforce_detection=False)
            emotion = analysis[0]["dominant_emotion"]
    except Exception as e:
        log.warning("DeepFace error: %s", e)

    return {
        "gaze": gaze,
        "gaze_ok": gaze == "Forward",
        "posture_ok": posture == "Straight",
        "emotion": emotion,
    }


def summarize_samples(samples: list[dict]) -> dict:
    """Aggregate all camera samples captured during one answer."""
    if not samples:
        return {"gaze_ok": False, "posture_ok": False, "emotion": "Unknown",
                "gaze_pct": 0, "posture_pct": 0, "camera_notes": "No camera samples captured."}
    n = len(samples)
    gaze_pct = round(100 * sum(bool(s.get("gaze_ok")) for s in samples) / n)
    posture_pct = round(100 * sum(bool(s.get("posture_ok")) for s in samples) / n)
    emotions = [s.get("emotion") for s in samples if s.get("emotion") not in (None, "Unknown")]
    emotion = Counter(emotions).most_common(1)[0][0] if emotions else "Unknown"
    return {
        "gaze_ok": gaze_pct >= 60,
        "posture_ok": posture_pct >= 60,
        "emotion": emotion,
        "gaze_pct": gaze_pct,
        "posture_pct": posture_pct,
        "camera_notes": f"Eye contact {gaze_pct}% of the time, upright posture {posture_pct}% "
                        f"({n} samples). Most frequent emotion: {emotion}.",
    }


class Camera:
    """Lazily-opened server webcam shared by all /video_feed clients."""

    def __init__(self, index: int = 0):
        self.index = index
        self._cap = None
        self._lock = threading.Lock()
        self._viewers = 0

    def frames(self):
        with self._lock:
            self._viewers += 1
            if self._cap is None or not self._cap.isOpened():
                self._cap = cv2.VideoCapture(self.index)
        try:
            while True:
                with self._lock:
                    ok, frame = self._cap.read() if self._cap is not None else (False, None)
                if not ok:
                    break
                ok, buf = cv2.imencode(".jpg", frame)
                if ok:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
        finally:
            # Last viewer gone (page closed / interview finished): release the webcam.
            with self._lock:
                self._viewers -= 1
                if self._viewers <= 0 and self._cap is not None:
                    self._cap.release()
                    self._cap = None
                    self._viewers = 0

    def release(self):
        with self._lock:
            if self._cap is not None:
                self._cap.release()
                self._cap = None


camera = Camera()
