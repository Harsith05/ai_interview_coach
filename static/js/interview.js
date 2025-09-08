(() => {
  const startBtn = document.getElementById('startBtn');
  const stopBtn = document.getElementById('stopBtn');
  const nextBtn = document.getElementById('nextBtn'); // NEXT button
  const questionEl = document.getElementById('question');
  const timerEl = document.getElementById('timer');
  const role = window.INTERVIEW_ROLE || 'ml';

  const timePerQuestion = 15;   // seconds per question
  const askCount = 3;           // number of questions to ask

  let questions = [];
  let currentIndex = 0;
  let countdownInterval = null;
  let running = false;

  // live voice
  let recognition = null;
  let interimText = '';
  let finalText = '';
  let liveSendInterval = null;

  // audio recording
  let mediaRecorder;
  let audioChunks = [];

  // collect results
  const results = [];

  // latest camera feedback
  let latestCameraFeedback = null;

  function ensureBox(id, label, css = {}) {
    let el = document.getElementById(id);
    if (!el) {
      el = document.createElement('div');
      el.id = id;
      el.style.marginTop = '10px';
      Object.assign(el.style, css);
      const qb = document.querySelector('.question-box');
      qb && qb.appendChild(el);
    }
    if (label) el.dataset.label = label;
    return el;
  }

  async function loadQuestions(role) {
    const res = await fetch(`/get_questions/${role}`);
    const data = await res.json();
    return data.slice(0, askCount);
  }

  function speak(text, onend) {
    if (!('speechSynthesis' in window)) {
      if (onend) onend();
      return;
    }
    const u = new SpeechSynthesisUtterance(text);
    u.lang = 'en-US';
    u.rate = 1;
    u.onend = () => onend && onend();
    try { speechSynthesis.cancel(); } catch {}
    speechSynthesis.speak(u);
  }

  function startTimer(s, onUp) {
    let left = s;
    clearInterval(countdownInterval);
    timerEl.textContent = left;
    countdownInterval = setInterval(() => {
      left -= 1;
      timerEl.textContent = left;
      if (left <= 0) {
        clearInterval(countdownInterval);
        timerEl.textContent = '0';
        onUp && onUp();
      }
    }, 1000);
  }

  function startSTT() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) return;
    recognition = new SR();
    recognition.lang = 'en-US';
    recognition.interimResults = true;
    recognition.continuous = true;
    finalText = '';
    interimText = '';

    const liveBox = ensureBox('live_transcript_box', 'Live Transcript', { fontSize: '13px', color: '#333' });

    recognition.onresult = (evt) => {
      let interim = '';
      for (let i = evt.resultIndex; i < evt.results.length; i++) {
        const res = evt.results[i];
        if (res.isFinal) finalText += res[0].transcript + ' ';
        else interim += res[0].transcript;
      }
      liveBox.innerHTML = `<strong>Live Transcript:</strong> ${finalText}${interim}`;
    };

    recognition.onerror = (e) => console.warn('STT error:', e);
    recognition.onend = () => {};
    recognition.start();

    clearInterval(liveSendInterval);
    liveSendInterval = setInterval(() => {
      const txt = (finalText || '').trim();
      if (txt.length > 0) {
        fetch('/score_answer', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            user_answer: txt,
            ideal_answers: (questions[currentIndex]?.answers || [])
          })
        }).then(r => r.json()).then(j => {
          const box = ensureBox('live_score_box', 'Live Score', { fontSize: '13px', color: '#444' });
          const sim = parseFloat(j.score || j.similarity || 0);
          const pct = Math.round(sim * 100);
          box.innerHTML = `<strong>Live Similarity:</strong> ${pct}%`;
        }).catch(() => {});
      }
    }, 2000);
  }

  function stopSTT() {
    if (recognition) {
      try { recognition.stop(); } catch {}
      recognition = null;
    }
    if (liveSendInterval) clearInterval(liveSendInterval);
    liveSendInterval = null;
  }

  let snapshotInterval = null;

  // ------------------------------
  // Webcam Setup
  // ------------------------------
  async function initCamera() {
    let videoEl = document.getElementById('video_stream');
    if (!videoEl) {
      videoEl = document.createElement('video');
      videoEl.id = 'video_stream';
      videoEl.autoplay = true;
      videoEl.playsInline = true;
      videoEl.width = 320;
      videoEl.height = 240;
      videoEl.style.display = "none"; // hide if not needed
      document.body.appendChild(videoEl);
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true });
      videoEl.srcObject = stream;
    } catch (err) {
      console.error("Camera error:", err);
    }
  }

  function startSnapshots() {
    const imgEl = document.getElementById('video_stream');
    snapshotInterval = setInterval(() => {
      try {
        const canvas = document.createElement('canvas');
        canvas.width = imgEl.videoWidth || 640;
        canvas.height = imgEl.videoHeight || 480;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(imgEl, 0, 0, canvas.width, canvas.height);
        const dataUrl = canvas.toDataURL('image/jpeg');
        fetch('/analyze_frame', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image: dataUrl })
        }).then(r => r.json()).then(j => {
          const ebox = ensureBox('emotion_box', 'Camera Feedback', { fontSize: '13px' });
          const g = j.gaze_ok ? '✅' : '⚠️';
          const p = j.posture_ok ? '✅' : '⚠️';
          const em = j.emotion || "Unknown";
          ebox.innerHTML = `<strong>Camera:</strong> Gaze ${g} | Posture ${p}<br/><em>Emotion: ${em}</em><br/><small>${j.notes || ''}</small>`;

          // store structured feedback
          latestCameraFeedback = {
            gaze: j.gaze_ok,
            posture: j.posture_ok,
            emotion: em,
            notes: j.notes || ''
          };
        }).catch(err => {
          console.warn("Frame analysis failed:", err);
        });
      } catch (e) {
        console.warn("Snapshot error:", e);
      }
    }, 3000);
  }

  function stopSnapshots() {
    if (snapshotInterval) clearInterval(snapshotInterval);
    snapshotInterval = null;
  }

  // ------------------------------
  // Next Button Handler
  // ------------------------------
  async function nextQuestionManually() {
    if (!running) return;
    clearInterval(countdownInterval);
    stopSTT();
    stopSnapshots();

    const user_answer = (finalText || '').trim();
    let score = 0;
    let transcript = user_answer;
    let speechRate = "N/A";
    let clarity = "";
    let tone = "";

    try {
      if (mediaRecorder && mediaRecorder.state !== "inactive") {
        await new Promise(resolve => {
          mediaRecorder.onstop = resolve;
          mediaRecorder.stop();
        });
      }
      const audioBlob = new Blob(audioChunks, { type: "audio/webm" });
      const formData = new FormData();
      formData.append("file", audioBlob, "answer.webm");
      formData.append("ideal_answers", (questions[currentIndex]?.answers || []).join("|"));
      const r = await fetch("/upload_audio", { method: "POST", body: formData });
      const j = await r.json();
      score = parseFloat(j.similarity || 0);
      transcript = j.transcript || user_answer;
      speechRate = (j.speech_rate && !isNaN(j.speech_rate)) ? `${j.speech_rate.toFixed(1)} WPM` : "N/A";
      clarity = j.clarity_notes || "";
      tone = j.tone_notes || "";
    } catch (e) {
      console.warn("Audio analysis failed", e);
    }

    const camFeedback = latestCameraFeedback || { gaze: false, posture: false, emotion: "Unknown", notes: "" };

    results.push({
      q: questions[currentIndex].question,
      ideal: questions[currentIndex].answers.join(" | "),
      transcript,
      score,
      camera_notes: camFeedback.notes,
      emotion: camFeedback.emotion,
      gaze_ok: camFeedback.gaze,
      posture_ok: camFeedback.posture,
      speech_rate: speechRate,
      clarity_notes: clarity,
      tone_notes: tone
    });

    currentIndex += 1;
    nextBtn.disabled = true;
    askCurrentQuestion();
  }

  // ------------------------------
  // Main Question Logic
  // ------------------------------
  async function askCurrentQuestion() {
    if (!running) return;
    if (currentIndex >= questions.length) {
      finishInterview();
      return;
    }

    const { question, answers } = questions[currentIndex];
    questionEl.textContent = question;

    nextBtn.disabled = false;

    // Start audio recording
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaRecorder = new MediaRecorder(stream);
      audioChunks = [];
      mediaRecorder.ondataavailable = e => {
        if (e.data.size > 0) audioChunks.push(e.data);
      };
      mediaRecorder.start();
    } catch (err) {
      console.error("Mic error:", err);
    }

    // Speak question, then start STT + snapshots
    speak(question, () => {
      startSTT();
      startSnapshots();

      startTimer(timePerQuestion, async () => {
        await nextQuestionManually(); // timer also uses same handler
      });
    });
  }

  // ------------------------------
  // Finish Interview
  // ------------------------------
  async function finishInterview() {
    running = false;
    startBtn.disabled = false;
    stopBtn.disabled = true;
    nextBtn.disabled = true;
    clearInterval(countdownInterval);
    try { speechSynthesis.cancel(); } catch {}

    const box = ensureBox('summary_box', 'Summary', { fontSize: '14px', background: '#fff', padding: '10px', borderRadius: '8px', boxShadow: '0 2px 8px rgba(0,0,0,0.06)' });
    const avg = results.length ? (results.reduce((a, b) => a + b.score, 0) / results.length) : 0;
    const avgPct = Math.round(avg * 100);

    const lines = results.map((r, i) => {
      const pct = Math.round(r.score * 100);
      return `<div style="margin-bottom:10px;">
        <strong>Q${i+1}:</strong> ${r.q}<br/>
        <strong>Your answer:</strong> ${r.transcript || '(no speech detected)'}<br/>
        <strong>Score:</strong> ${pct}%<br/>
        <strong>Speech Rate:</strong> ${r.speech_rate || 'N/A'}<br/>
        <strong>Clarity:</strong> ${r.clarity_notes || ''}<br/>
        <strong>Tone:</strong> ${r.tone_notes || ''}<br/>
        <strong>Camera:</strong> Gaze ${r.gaze_ok ? '✅' : '⚠️'} | Posture ${r.posture_ok ? '✅' : '⚠️'}<br/>
        <strong>Emotion:</strong> ${r.emotion}<br/>
        <details><summary>Reveal ideal answers</summary>${(r.ideal || '').replace(/\|/g, '<br/>')}</details>
      </div>`;
    }).join('');

    const feedbackResp = await fetch('/generate_feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        answer: results.map(r => r.transcript).join("\n"),
        similarity: avg,
        emotion: results.map(r => r.emotion).join(", "),
        voice_tone: results.map(r => r.tone_notes).join(", "),
        eye_gaze: results.map(r => r.camera_notes).join(", ")
      })
    });
    const feedbackData = await feedbackResp.json();
    const llmFeedback = feedbackData.feedback || "No feedback generated.";

    box.innerHTML = `<div><strong>Interview complete.</strong> Average similarity: <strong>${avgPct}%</strong></div>
      <hr/>${lines}<hr/>
      <div><strong>Feedback:</strong>
        <p>${llmFeedback}</p>
      </div>`;

    questionEl.textContent = "Interview complete. Review your feedback below.";
    timerEl.textContent = "--";
  }

  async function startInterview() {
    if (running) return;
    running = true;
    startBtn.disabled = true;
    stopBtn.disabled = false;
    nextBtn.disabled = true;
    questionEl.textContent = "Preparing interview...";
    timerEl.textContent = "--";

    try {
      await initCamera();
      questions = await loadQuestions(role);
    } catch {
      questions = [];
    }
    if (!questions.length) {
      questionEl.textContent = "No questions found for this role.";
      startBtn.disabled = false;
      stopBtn.disabled = true;
      running = false;
      return;
    }

    currentIndex = 0;
    setTimeout(askCurrentQuestion, 300);
  }

  function stopInterview() {
    running = false;
    startBtn.disabled = false;
    stopBtn.disabled = true;
    nextBtn.disabled = true;
    clearInterval(countdownInterval);
    stopSTT();
    stopSnapshots();
    try { speechSynthesis.cancel(); } catch {}
    questionEl.textContent = "Interview stopped.";
    timerEl.textContent = "--";
  }

  // Event Listeners
  startBtn.addEventListener('click', startInterview);
  stopBtn.addEventListener('click', stopInterview);
  nextBtn.addEventListener('click', nextQuestionManually);
})();


