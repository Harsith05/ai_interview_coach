// interview.js — Real-time STT, scoring, and video heuristics

(() => {
  const startBtn = document.getElementById('startBtn');
  const stopBtn = document.getElementById('stopBtn');
  const questionEl = document.getElementById('question');
  const timerEl = document.getElementById('timer');
  const role = window.INTERVIEW_ROLE || 'ml';

  const timePerQuestion = 20;   // seconds per question
  const askCount = 5;           // reveal answers after these many questions

  let questions = [];           // [{question, ideal}, ...]
  let currentIndex = 0;
  let countdownInterval = null;
  let running = false;

  // live voice
  let recognition = null;
  let interimText = '';
  let finalText = '';
  let liveSendInterval = null;

  // collect results to reveal answers at the end
  const results = []; // [{q, ideal, transcript, score, gaze_ok, posture_ok}, ...]

  // UI helpers
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

  // Load questions
  async function loadQuestions(role) {
    const res = await fetch(`/get_questions/${role}`);
    const data = await res.json();
    return data.slice(0, askCount);
  }

  // TTS
  function speak(text, onend) {
    if (!('speechSynthesis' in window)) {
      console.warn('speechSynthesis not supported');
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

  // Timer
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

  // Real-time STT (Web Speech API)
  function startSTT() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      console.warn('SpeechRecognition not supported in this browser.');
      return;
    }
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
        if (res.isFinal) {
          finalText += res[0].transcript + ' ';
        } else {
          interim += res[0].transcript;
        }
      }
      liveBox.innerHTML = `<strong>Live Transcript:</strong> ${finalText}${interim}`;
    };

    recognition.onerror = (e) => console.warn('STT error:', e);
    recognition.onend = () => {/* restarted per question */};
    recognition.start();

    // Optional: stream partial transcript to backend (for future real-time use)
    clearInterval(liveSendInterval);
    liveSendInterval = setInterval(() => {
      const txt = (finalText || '').trim();
      if (txt.length > 0) {
        fetch('/score_answer', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            user_answer: txt,
            ideal_answer: questions[currentIndex]?.ideal || ''
          })
        }).then(r => r.json()).then(j => {
          const box = ensureBox('live_score_box', 'Live Score', { fontSize: '13px', color: '#444' });
          const pct = Math.round((j.score || 0) * 100);
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
    if (liveSendInterval) {
      clearInterval(liveSendInterval);
      liveSendInterval = null;
    }
  }

  // Snapshot to backend for gaze/posture every 3s while answering
  let snapshotInterval = null;
  function startSnapshots() {
    const imgEl = document.getElementById('video_stream');
    snapshotInterval = setInterval(() => {
      try {
        const canvas = document.createElement('canvas');
        canvas.width = imgEl.naturalWidth || imgEl.width;
        canvas.height = imgEl.naturalHeight || imgEl.height;
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
          ebox.innerHTML = `<strong>Camera:</strong> Gaze ${g} | Posture ${p}<br/><em>${j.notes || ''}</em>`;
        }).catch(() => {});
      } catch (e) {}
    }, 3000);
  }
  function stopSnapshots() {
    if (snapshotInterval) clearInterval(snapshotInterval);
    snapshotInterval = null;
  }

  // Ask the current question
  function askCurrentQuestion() {
    if (!running) return;
    if (currentIndex >= questions.length) {
      finishInterview();
      return;
    }

    const { question, ideal } = questions[currentIndex];
    questionEl.textContent = question;

    // Speak question, then start STT + timer + snapshots
    speak(question, () => {
      startSTT();
      startSnapshots();

      startTimer(timePerQuestion, async () => {
        // time up for this question
        stopSTT();
        stopSnapshots();

        // final transcript to score
        const user_answer = (finalText || '').trim();
        let score = 0;
        try {
          const r = await fetch('/score_answer', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_answer, ideal_answer: ideal })
          });
          const j = await r.json();
          score = j.score || 0;
        } catch (e) { /* keep score=0 */ }

        // (optional) get last camera feedback shown on screen (not stored server-side per Q)
        const camBox = document.getElementById('emotion_box');
        const camText = camBox ? camBox.innerText : '';

        results.push({
          q: question,
          ideal,
          transcript: user_answer,
          score,
          camera_notes: camText
        });

        // Small pause, then next
        currentIndex += 1;
        setTimeout(askCurrentQuestion, 600);
      });
    });
  }

  function finishInterview() {
    running = false;
    startBtn.disabled = false;
    stopBtn.disabled = true;
    clearInterval(countdownInterval);
    try { speechSynthesis.cancel(); } catch {}

    // Build summary: reveal answers now
    const box = ensureBox('summary_box', 'Summary', { fontSize: '14px', background: '#fff', padding: '10px', borderRadius: '8px', boxShadow: '0 2px 8px rgba(0,0,0,0.06)' });
    const avg = results.length ? (results.reduce((a, b) => a + b.score, 0) / results.length) : 0;
    const avgPct = Math.round(avg * 100);

    const lines = results.map((r, i) => {
      const pct = Math.round(r.score * 100);
      return `
        <div style="margin-bottom:10px;">
          <strong>Q${i+1}:</strong> ${r.q}<br/>
          <strong>Your answer:</strong> ${r.transcript || '(no speech detected)'}<br/>
          <strong>Score:</strong> ${pct}%<br/>
          <details><summary>Reveal ideal answer</summary>${r.ideal || '(no reference answer)'}</details>
        </div>
      `;
    }).join('');

    const suggestions = [
      avg < 0.6 ? "Try structuring answers with a brief definition, 2–3 key points, and an example." : "Great content coverage—keep adding concise examples.",
      "Maintain eye contact with the camera and stay centered in frame.",
      "Sit upright; keep shoulders open; minimize fidgeting.",
      "Speak clearly at a steady pace; pause briefly before key points."
    ];

    box.innerHTML = `
      <div><strong>Interview complete.</strong> Average similarity: <strong>${avgPct}%</strong></div>
      <hr/>
      ${lines}
      <hr/>
      <div><strong>Feedback:</strong>
        <ul style="margin-top:6px;text-align:left;">
          ${suggestions.map(s => `<li>${s}</li>`).join('')}
        </ul>
      </div>
    `;

    questionEl.textContent = "Interview complete. Review your feedback below.";
    timerEl.textContent = "--";
  }

  async function startInterview() {
    if (running) return;
    running = true;
    startBtn.disabled = true;
    stopBtn.disabled = false;
    questionEl.textContent = "Preparing interview...";
    timerEl.textContent = "--";

    try {
      questions = await loadQuestions(role);    // expects [{question, ideal}, ...]
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
    clearInterval(countdownInterval);
    stopSTT();
    stopSnapshots();
    try { speechSynthesis.cancel(); } catch {}
    questionEl.textContent = "Interview stopped.";
    timerEl.textContent = "--";
  }

  // events
  startBtn.addEventListener('click', startInterview);
  stopBtn.addEventListener('click', stopInterview);
})();
