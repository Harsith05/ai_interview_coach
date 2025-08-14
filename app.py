from flask import Flask, render_template, request, redirect, url_for, Response, jsonify
import csv
import random
import cv2

app = Flask(__name__)

# ---------- Webcam setup ----------
camera = cv2.VideoCapture(0)  # default webcam index

def generate_frames():
    while True:
        success, frame = camera.read()
        if not success:
            break
        else:
            ret, buffer = cv2.imencode('.jpg', frame)
            frame = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

# ---------- Questions loader ----------
def load_questions_from_csv(domain, count=6):
    """
    Load questions for a domain from CSV files inside data/.
    Will return up to `count` random questions from the file for the domain.
    Expected CSV files: data/ml_questions.csv, data/frontend_questions.csv, etc.
    CSV format: question (single column)
    """
    domain_map = {
        'ml': 'data/ml_questions.csv',
        'frontend': 'data/frontend_questions.csv',
        'backend': 'data/backend_questions.csv',
        'data_analyst': 'data/data_analyst_questions.csv'
    }
    domain = domain.lower()
    filename = domain_map.get(domain)
    questions = []
    if filename:
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                for row in reader:
                    if not row:
                        continue
                    # assume the question is in the first column
                    questions.append(row[0].strip())
        except FileNotFoundError:
            print(f"Question file not found: {filename}")
    # pick up to `count` random questions
    if len(questions) > count:
        questions = random.sample(questions, count)
    return questions

# ---------- Routes ----------
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        role = request.form.get('role')
        return redirect(url_for('interview', role=role))
    return render_template('index.html')

@app.route('/interview/<role>')
def interview(role):
    # Interview page will fetch questions with JS using get_questions route
    return render_template('interview.html', role=role)

@app.route('/get_questions/<role>')
def get_questions(role):
    # Returns JSON list of questions for the requested role
    questions = load_questions_from_csv(role, count=6)  # 5-8 recommended; 6 default
    return jsonify(questions)

@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

# Graceful cleanup on shutdown
import atexit
@atexit.register
def cleanup():
    try:
        camera.release()
    except Exception:
        pass

if __name__ == "__main__":
    app.run(debug=True)