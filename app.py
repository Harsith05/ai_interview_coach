from flask import Flask, render_template, request, redirect, url_for, Response, jsonify
import csv, random, io, base64
import cv2
import numpy as np

# --- NLP scoring (TF-IDF cosine) ---
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

app = Flask(__name__)

# ---------------- Webcam stream ----------------
camera = cv2.VideoCapture(0)

def generate_frames():
    while True:
        success, frame = camera.read()
        if not success:
            break
        ret, buffer = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        frame = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

# ---------------- Data loading ----------------
def load_qa_from_csv(domain, count=5):
    """
    CSV format expected: question,answer
    Files under data/: ml_questions.csv, frontend_questions.csv, backend_questions.csv, data_analyst_questions.csv
    Returns list[ {question: str, ideal: str} ]
    """
    domain_map = {
        'ml': 'data/ml_questions.csv',
        'frontend': 'data/frontend_questions.csv',
        'backend': 'data/backend_questions.csv',
        'data_analyst': 'data/data_analyst_questions.csv'
    }
    filename = domain_map.get(domain.lower())
    qa = []
    if filename:
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                for row in reader:
                    if not row or len(row) < 1:
                        continue
                    q = row[0].strip()
                    a = row[1].strip() if len(row) > 1 else ""
                    if q:
                        qa.append({"question": q, "ideal": a})
        except FileNotFoundError:
            print(f"[WARN] Question file not found: {filename}")
    random.shuffle(qa)
    return qa[:count]

# ---------------- Simple NLP scoring ----------------
def similarity_score(user_answer: str, ideal_answer: str) -> float:
    """
    TF-IDF cosine similarity between user answer and ideal answer.
    Returns 0.0 .. 1.0
    """
    user_answer = (user_answer or "").strip().lower()
    ideal_answer = (ideal_answer or "").strip().lower()
    if not user_answer or not ideal_answer:
        return 0.0
    vect = TfidfVectorizer(stop_words='english')
    X = vect.fit_transform([user_answer, ideal_answer])
    sim = cosine_similarity(X[0:1], X[1:2])[0, 0]
    # clamp
    return float(max(0.0, min(1.0, sim)))

# ---------------- Very light video heuristics ----------------
# Using Haar cascades (bundled with OpenCV) to locate face & eyes.
face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
eye_cascade  = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_eye.xml")

def analyze_frame_b64(data_url: str):
    """
    Expects data_url like 'data:image/jpeg;base64,...'
    Returns dict with gaze_ok, posture_ok, notes
    Heuristics:
      - gaze_ok: eyes detected and face center near frame center
      - posture_ok: face size reasonable (not too close/far), basically upright bbox
    """
    try:
        header, b64 = data_url.split(',', 1)
        img_bytes = base64.b64decode(b64)
        img_arr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
        if img is None:
            return {"gaze_ok": False, "posture_ok": False, "notes": "Could not decode image"}

        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, 1.2, 5)

        if len(faces) == 0:
            return {"gaze_ok": False, "posture_ok": False, "notes": "No face detected; look toward camera and ensure lighting"}

        # Choose largest face
        faces = sorted(faces, key=lambda r: r[2]*r[3], reverse=True)
        (x, y, fw, fh) = faces[0]
        face_center = (x + fw/2, y + fh/2)
        frame_center = (w/2, h/2)
        center_dx = abs(face_center[0] - frame_center[0]) / (w/2)
        center_dy = abs(face_center[1] - frame_center[1]) / (h/2)

        # eyes detection for gaze
        roi_gray = gray[y:y+fh, x:x+fw]
        eyes = eye_cascade.detectMultiScale(roi_gray, 1.2, 5)

        gaze_ok = (len(eyes) >= 1) and (center_dx < 0.35) and (center_dy < 0.35)

        # posture proxy: face size proportion should be within limits
        face_area_ratio = (fw * fh) / float(w * h)
        posture_ok = 0.03 <= face_area_ratio <= 0.45  # rough bounds: not too far/close

        notes = []
        if not gaze_ok:
            notes.append("Keep your eyes toward the camera; center your face in the frame.")
        if not posture_ok:
            notes.append("Adjust distance to the camera; keep an upright posture.")

        return {"gaze_ok": bool(gaze_ok), "posture_ok": bool(posture_ok), "notes": " ".join(notes) or "Good camera presence"}
    except Exception as e:
        return {"gaze_ok": False, "posture_ok": False, "notes": f"Error analyzing frame: {e}"}

# ---------------- Routes ----------------

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        role = request.form.get('role', 'ml')
        return redirect(url_for('interview', role=role))
    return render_template('index.html')  # uploaded as Index.html.docx → index.html  :contentReference[oaicite:0]{index=0}

@app.route('/interview/<role>')
def interview(role):
    return render_template('interview.html', role=role)  # uploaded Interview.html  :contentReference[oaicite:1]{index=1}

@app.route('/get_questions/<role>')
def get_questions(role):
    # Return objects so frontend has {question, ideal}
    qa = load_qa_from_csv(role, count=5)  # ask 5 Qs; change to 6–8 if you like
    return jsonify(qa)

@app.route('/score_answer', methods=['POST'])
def score_answer():
    data = request.get_json(force=True)
    user_answer = data.get("user_answer", "")
    ideal_answer = data.get("ideal_answer", "")
    score = similarity_score(user_answer, ideal_answer)  # 0..1
    return jsonify({"score": score})

@app.route('/analyze_frame', methods=['POST'])
def analyze_frame():
    payload = request.get_json(force=True)
    data_url = payload.get("image", "")
    result = analyze_frame_b64(data_url)
    return jsonify(result)

@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

# Cleanup
import atexit
@atexit.register
def _cleanup():
    try:
        camera.release()
    except Exception:
        pass

if __name__ == "__main__":
    app.run(debug=True)
