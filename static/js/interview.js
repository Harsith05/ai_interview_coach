(() => {
  const CFG = window.INTERVIEW;
  const $ = (id) => document.getElementById(id);
  const startBtn = $('startBtn'), stopBtn = $('stopBtn'), nextBtn = $('nextBtn');
  const questionEl = $('question'), qmetaEl = $('qmeta'), timerEl = $('timer');
  const progressEl = $('progress'), nudgeEl = $('nudge');

  // Seconds per answer. "Next" ends an answer early.
  const ANSWER_TIME = CFG.mode === 'behavioral' ? 120 : 60;
  const FOLLOW_UP_TIME = 45;

  let currentQuestion = null;   // payload from the server: {turn, number, total, question, ...}
  let running = false;
  let submitting = false;
  let countdownInterval = null;

  // ------------------------------------------------------------ helpers
  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function ensureBox(id, css = {}) {
    let el = $(id);
    if (!el) {
      el = document.createElement('div');
      el.id = id;
      el.className = 'side-box';
      Object.assign(el.style, css);
      document.querySelector('.question-box').appendChild(el);
    }
    return el;
  }

  // Allow-list sanitizer for the LLM's streamed HTML (the final version is also cleaned server-side).
  const ALLOWED = new Set(['H3', 'H4', 'H5', 'P', 'UL', 'OL', 'LI', 'STRONG', 'EM', 'B', 'I', 'BR', 'HR', 'SPAN', 'DIV', 'CODE', 'BLOCKQUOTE']);
  function sanitize(html) {
    const doc = new DOMParser().parseFromString(`<div>${html}</div>`, 'text/html');
    const walk = (node) => {
      for (const child of [...node.childNodes]) {
        if (child.nodeType === 1) {
          if (!ALLOWED.has(child.tagName)) { child.replaceWith(...child.childNodes); walk(node); return; }
          for (const a of [...child.attributes]) child.removeAttribute(a.name);
          walk(child);
        } else if (child.nodeType !== 3) child.remove();
      }
    };
    const root = doc.body.firstChild;
    walk(root);
    return root.innerHTML;
  }

  async function readNdjson(response, onEvent) {
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || response.statusText);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (line) await onEvent(JSON.parse(line));
      }
    }
  }

  function setProgress(fraction) {
    progressEl.style.width = `${Math.max(0, Math.min(1, fraction)) * 100}%`;
  }

  // ------------------------------------------------------------ speech synthesis
  let currentUtterance = null; // keep a reference: Chromium can GC it and never fire onend
  function speak(text, onend) {
    let done = false;
    const finish = () => { if (!done) { done = true; onend && onend(); } };
    if (!('speechSynthesis' in window)) { finish(); return; }
    const u = new SpeechSynthesisUtterance(text);
    currentUtterance = u;
    u.lang = 'en-US';
    u.rate = 1;
    u.onend = finish;
    u.onerror = finish;
    try { speechSynthesis.cancel(); } catch {}
    speechSynthesis.speak(u);
    setTimeout(finish, text.split(/\s+/).length * 500 + 2000); // fallback if onend never fires
  }

  function startTimer(s, onUp) {
    let left = s;
    clearInterval(countdownInterval);
    timerEl.textContent = left;
    countdownInterval = setInterval(() => {
      left -= 1;
      timerEl.textContent = left;
      if (left === 10) nudge('⏱ 10 seconds left: wrap up your answer.', 4000);
      if (left <= 0) { clearInterval(countdownInterval); timerEl.textContent = '0'; onUp && onUp(); }
    }, 1000);
  }

  // ------------------------------------------------------------ live transcript (browser)
  let recognition = null, finalText = '', liveScoreInterval = null, answerStartedAt = 0;

  function startSTT() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    finalText = '';
    if (!SR) return;
    recognition = new SR();
    recognition.lang = 'en-US';
    recognition.interimResults = true;
    recognition.continuous = true;
    const liveBox = ensureBox('live_transcript_box', { color: '#333' });
    recognition.onresult = (evt) => {
      let interim = '';
      for (let i = evt.resultIndex; i < evt.results.length; i++) {
        const res = evt.results[i];
        if (res.isFinal) finalText += res[0].transcript + ' ';
        else interim += res[0].transcript;
      }
      liveBox.innerHTML = `<strong>Live transcript:</strong> ${escapeHtml(finalText + interim)}`;
    };
    recognition.onerror = (e) => console.warn('STT error:', e.error);
    const rec = recognition;
    recognition.onend = () => { if (recognition === rec && running && !submitting) { try { rec.start(); } catch {} } };
    recognition.start();

    clearInterval(liveScoreInterval);
    if (CFG.mode !== 'behavioral') {
      let inFlight = false; // never queue requests on the server: skip a tick if one is pending
      liveScoreInterval = setInterval(() => {
        const txt = finalText.trim();
        if (!txt || !currentQuestion || inFlight) return;
        inFlight = true;
        fetch('/api/score_answer', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_answer: txt, ideal_answers: currentQuestion.key_points || [] }),
        }).then(r => r.json()).then(j => {
          ensureBox('live_score_box', { color: '#444' }).innerHTML =
            `<strong>Coverage of key points:</strong> ${Math.round((j.score || 0) * 100)}%`;
        }).catch(() => {}).finally(() => { inFlight = false; });
      }, 3000);
    }
  }

  function stopSTT() {
    if (recognition) { const rec = recognition; recognition = null; try { rec.stop(); } catch {} }
    clearInterval(liveScoreInterval);
  }

  // ------------------------------------------------------------ camera
  let snapshotInterval = null, cameraSamples = [];

  async function initBrowserCamera() {
    const v = $('video_stream');
    if (CFG.serverCamera) {
      if (!v.getAttribute('src')) v.src = '/video_feed';
      return;
    }
    try {
      if (!v.srcObject) v.srcObject = await navigator.mediaDevices.getUserMedia({ video: true });
    } catch (e) { console.warn('Camera error', e); }
  }

  // Stop the webcam stream: frees the camera, the server worker streaming it, and one of the
  // browser's (max 6) connections to this site.
  function stopCamera() {
    const v = $('video_stream');
    if (!v) return;
    if (CFG.serverCamera) {
      v.removeAttribute('src');
    } else if (v.srcObject) {
      v.srcObject.getTracks().forEach(t => t.stop());
      v.srcObject = null;
    }
  }

  function startSnapshots() {
    const src = $('video_stream');
    const bucket = cameraSamples = []; // per-question, so late responses can't leak into the next one
    let inFlight = false;             // one frame at a time: never build a queue on the server
    snapshotInterval = setInterval(() => {
      if (inFlight || !(src.videoWidth || src.naturalWidth)) return;
      inFlight = true;
      try {
        const w = src.videoWidth || src.naturalWidth || 640, h = src.videoHeight || src.naturalHeight || 480;
        const canvas = document.createElement('canvas');
        canvas.width = w; canvas.height = h;
        canvas.getContext('2d').drawImage(src, 0, 0, w, h);
        fetch('/api/analyze_frame', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image: canvas.toDataURL('image/jpeg', 0.8) }),
        }).then(r => r.ok ? r.json() : null).then(j => {
          if (!j) return;
          bucket.push(j);
          ensureBox('emotion_box').innerHTML =
            `<strong>Camera:</strong> Gaze ${j.gaze_ok ? '✅' : '⚠️'} · Posture ${j.posture_ok ? '✅' : '⚠️'} · <em>${escapeHtml(j.emotion)}</em>`;
        }).catch(() => {}).finally(() => { inFlight = false; });
      } catch (e) { inFlight = false; console.warn('Snapshot error:', e); }
    }, 3000);
  }

  function stopSnapshots() { clearInterval(snapshotInterval); snapshotInterval = null; }

  // ------------------------------------------------------------ microphone picker + meter
  const VIRTUAL_MIC = /voicemeeter|vb-audio|cable output|virtual|stereo mix|what u hear|loopback/i;
  const MIC_KEY = 'aic.micDeviceId';
  let micSelect = null;
  const savedMic = () => { try { return localStorage.getItem(MIC_KEY); } catch { return null; } };
  const saveMic = (id) => { try { localStorage.setItem(MIC_KEY, id); } catch {} };

  async function setupMicPicker() {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    if (!micSelect) {
      const wrap = document.createElement('div');
      wrap.style.cssText = 'margin-top:12px;font-size:13px';
      wrap.innerHTML = '<label for="micSelect" style="display:inline;margin:0">Microphone:</label> ';
      micSelect = document.createElement('select');
      micSelect.id = 'micSelect';
      micSelect.style.cssText = 'max-width:100%;font-size:13px;padding:4px;width:auto';
      micSelect.addEventListener('change', () => saveMic(micSelect.value));
      wrap.appendChild(micSelect);
      document.querySelector('.controls').after(wrap);
    }
    let devices = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'audioinput');
    if (devices.length && !devices.some(d => d.label)) {
      try {
        const s = await navigator.mediaDevices.getUserMedia({ audio: true });
        s.getTracks().forEach(t => t.stop());
        devices = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'audioinput');
      } catch {}
    }
    const real = devices.filter(d => !['default', 'communications'].includes(d.deviceId));
    const list = real.length ? real : devices;
    const saved = savedMic();
    const pick = list.find(d => d.deviceId === saved) || list.find(d => !VIRTUAL_MIC.test(d.label)) || list[0];
    micSelect.innerHTML = list.map(d =>
      `<option value="${escapeHtml(d.deviceId)}">${escapeHtml(d.label || 'Microphone')}${VIRTUAL_MIC.test(d.label) ? ' (virtual)' : ''}</option>`).join('');
    if (pick) micSelect.value = pick.deviceId;
  }

  const micConstraints = () => ({ audio: micSelect?.value ? { deviceId: { exact: micSelect.value } } : true });

  let audioCtx = null, meterRaf = null, lastVoiceAt = 0, heardVoice = false;

  function startMeter(stream) {
    const box = ensureBox('mic_box');
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      audioCtx.resume();
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 512;
      audioCtx.createMediaStreamSource(stream).connect(analyser);
      const buf = new Uint8Array(analyser.fftSize);
      const label = escapeHtml(stream.getAudioTracks()[0]?.label || 'unknown microphone');
      const started = performance.now();
      heardVoice = false; lastVoiceAt = 0;
      const tick = () => {
        analyser.getByteTimeDomainData(buf);
        let peak = 0;
        for (const v of buf) peak = Math.max(peak, Math.abs(v - 128));
        const pct = Math.min(100, Math.round((peak / 128) * 250));
        window.Robot?.level(pct);
        if (pct > 8) { heardVoice = true; lastVoiceAt = performance.now(); }
        const warn = !heardVoice && performance.now() - started > 4000
          ? `<div style="color:var(--danger)">⚠️ We can't hear you. Pick your real microphone in the list above and make sure it isn't muted.</div>` : '';
        box.innerHTML = `<strong>🎤</strong> <small>${label}</small>
          <div style="height:8px;background:#eee;border-radius:4px;margin-top:4px">
            <div style="height:8px;width:${pct}%;background:${pct > 8 ? 'var(--primary)' : '#bbb'};border-radius:4px"></div></div>${warn}`;
        meterRaf = requestAnimationFrame(tick);
      };
      tick();
    } catch (e) { console.warn('Mic meter unavailable', e); }
  }

  function stopMeter() { if (meterRaf) cancelAnimationFrame(meterRaf); meterRaf = null; }

  // ------------------------------------------------------------ recording
  let mediaRecorder = null, micStream = null, audioChunks = [];

  async function startRecording() {
    audioChunks = [];
    try {
      micStream = await navigator.mediaDevices.getUserMedia(micConstraints());
      mediaRecorder = new MediaRecorder(micStream);
      mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };
      mediaRecorder.start(1000);
      startMeter(micStream);
    } catch (err) {
      console.error('Mic error:', err);
      mediaRecorder = null;
      ensureBox('mic_box').innerHTML =
        `<span style="color:var(--danger)">⚠️ Microphone unavailable: ${escapeHtml(err.message || err.name)}.</span>`;
    }
  }

  async function stopRecording() {
    stopMeter();
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
      await new Promise(resolve => { mediaRecorder.onstop = resolve; mediaRecorder.stop(); });
    }
    if (micStream) micStream.getTracks().forEach(t => t.stop());
    micStream = null; mediaRecorder = null;
    return audioChunks.length ? new Blob(audioChunks, { type: 'audio/webm' }) : null;
  }

  // ------------------------------------------------------------ live coaching nudges
  let nudgeInterval = null, nudgeTimeout = null, lastNudgeAt = 0;

  function nudge(text, ms = 3500) {
    lastNudgeAt = performance.now();
    nudgeEl.textContent = text;
    clearTimeout(nudgeTimeout);
    nudgeTimeout = setTimeout(() => { nudgeEl.textContent = ''; }, ms);
  }

  function startNudges() {
    clearInterval(nudgeInterval);
    nudgeInterval = setInterval(() => {
      if (!running || submitting || performance.now() - lastNudgeAt < 6000) return;
      const now = performance.now();
      const elapsed = (now - answerStartedAt) / 1000;
      const words = finalText.trim() ? finalText.trim().split(/\s+/) : [];
      const fillers = words.filter(w => /^(um+|uh+|erm|hmm+)$/i.test(w)).length;
      const recent = cameraSamples.slice(-2);

      if (heardVoice && now - lastVoiceAt > 5000) nudge('💬 Keep going: explain your reasoning or give an example.');
      else if (elapsed > 12 && words.length / (elapsed / 60) > 175) nudge('🐢 You\'re speaking fast. Slow down a little.');
      else if (fillers >= 3) nudge('⏸ Try a short pause instead of "um".');
      else if (recent.length === 2 && recent.every(s => !s.gaze_ok)) nudge('👀 Look at the camera.');
      else if (recent.length === 2 && recent.every(s => !s.posture_ok)) nudge('🪑 Sit up straight.');
      else if (CFG.mode === 'behavioral' && elapsed > 45 && !/result|outcome|learn|impact|improv/i.test(finalText))
        nudge('⭐ Don\'t forget the Result: what was the outcome?');
    }, 1500);
  }

  function stopNudges() { clearInterval(nudgeInterval); nudgeEl.textContent = ''; }

  // ------------------------------------------------------------ interview flow
  function showMeta(q) {
    const diff = q.difficulty ? `<span class="badge ${q.difficulty === 'hard' ? 'red' : q.difficulty === 'easy' ? 'green' : 'orange'}">${escapeHtml(q.difficulty)}</span>` : '';
    const fu = q.is_follow_up ? '<span class="badge orange">Follow-up</span>' : '';
    const topic = q.topic ? `<span class="badge">${escapeHtml(q.topic)}</span>` : '';
    qmetaEl.innerHTML = `<strong>Question ${q.number} of ${q.total}</strong> ${fu} ${diff} ${topic}`;
    setProgress((q.number - (q.is_follow_up ? 0.5 : 1)) / q.total);
  }

  function setStatus(text) {
    questionEl.textContent = text;
  }

  function askQuestion(q) {
    if (!running) return;
    currentQuestion = q;
    showMeta(q);
    questionEl.textContent = q.question;
    nextBtn.disabled = false;
    timerEl.textContent = '--';
    ['live_transcript_box', 'live_score_box'].forEach(id => $(id)?.remove());

    speak(q.question, async () => {
      if (!running || submitting || currentQuestion !== q) return;
      await startRecording();
      if (!running || submitting || currentQuestion !== q) { await stopRecording(); return; }
      answerStartedAt = performance.now();
      window.Robot?.state('listening');
      startSTT();
      startSnapshots();
      startNudges();
      startTimer(q.is_follow_up ? FOLLOW_UP_TIME : ANSWER_TIME, submitAnswer);
    });
  }

  // ---------------------------------------------------------------- "Generating your report" card
  // After the last answer the server analyzes every answer, grades them, summarizes and writes the
  // report (20-60 s). Each step arrives as a `status` event with a `stage`; this card shows them.
  const REPORT_STAGES = ['analyze', 'grade', 'summarize', 'write'];
  const rp = { el: $('report-progress'), stage: -1, started: 0, timer: null, analyzed: 0, total: 0 };

  function rpTick() {
    const s = Math.floor((Date.now() - rp.started) / 1000);
    $('rpTime').textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    if (s === 75) $('rpSub').textContent = 'Taking a little longer than usual (the AI may be busy). Still working…';
  }

  function rpRender() {
    rp.el.querySelectorAll('.rp-steps li').forEach((li, i) => {
      li.classList.toggle('done', i < rp.stage);
      li.classList.toggle('active', i === rp.stage);
    });
    // Each stage is a quarter of the bar; answer analysis fills its quarter answer by answer.
    let frac = (rp.stage + 0.5) / REPORT_STAGES.length;
    if (rp.stage === 0 && rp.total) frac = (0.15 + 0.85 * rp.analyzed / rp.total) / REPORT_STAGES.length;
    $('rpBar').style.width = `${Math.round(Math.max(frac, 0.04) * 100)}%`;
  }

  function setReportStage(stage) {
    const idx = REPORT_STAGES.indexOf(stage);
    if (idx < 0 || idx < rp.stage) return;   // never go backwards (analysis runs in parallel)
    if (rp.el.hidden) {
      rp.el.hidden = false;
      rp.el.classList.remove('rp-ready');
      $('rpTitle').textContent = 'Generating your report…';
      rp.started = Date.now();
      rpTick();
      clearInterval(rp.timer);
      rp.timer = setInterval(rpTick, 1000);
      rp.el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      window.Robot?.say('Great job! Give me a moment to prepare your report.');
    }
    rp.stage = idx;
    rpRender();
  }

  function setAnalyzeProgress(done, total) {
    rp.analyzed = done; rp.total = total;
    $('rpAnalyzeNote').textContent = `${done} of ${total} answer${total === 1 ? '' : 's'} analyzed`;
    rpRender();
  }

  function finishReportProgress() {
    if (rp.el.hidden) return;
    clearInterval(rp.timer);
    rp.stage = REPORT_STAGES.length;
    rpRender();
    $('rpBar').style.width = '100%';
    rp.el.classList.add('rp-ready');
    $('rpTitle').textContent = 'Your report is ready';
    $('rpSub').textContent = `Done in ${$('rpTime').textContent}. Scroll down to read it.`;
    setTimeout(() => { rp.el.hidden = true; }, 2500);
  }

  function hideReportProgress() {
    clearInterval(rp.timer);
    rp.el.hidden = true;
    rp.stage = -1;
  }

  // ---------------------------------------------------------------- rate this interview
  const rateStars = [...document.querySelectorAll('#rateStars button')];
  const paintRate = (n) => rateStars.forEach((b, i) => b.classList.toggle('on', i < n));
  rateStars.forEach((b, i) => {
    b.addEventListener('mouseenter', () => paintRate(i + 1));
    b.addEventListener('focus', () => paintRate(i + 1));
    b.addEventListener('click', () => window.openFeedback?.({
      rating: i + 1, interviewId: CFG.id, kind: i + 1 >= 4 ? 'praise' : 'other',
      title: 'Rate this interview',
      intro: 'Thanks for rating! Anything that worked well, or that we should improve? (optional)',
    }));
  });
  $('rateStars')?.addEventListener('mouseleave', () => paintRate(0));
  document.addEventListener('feedback:sent', (e) => {
    if (e.detail?.interviewId !== CFG.id || !e.detail.rating) return;
    $('rateStars').hidden = true;
    $('rateLabel').textContent = `Thanks for rating this interview ${'★'.repeat(e.detail.rating)}!`;
  });

  // Shared handler for every streamed event from the server.
  let streamedHtml = '';
  async function handleEvent(ev) {
    switch (ev.type) {
      case 'status':
        setStatus(ev.text);
        window.Robot?.state('thinking', ev.text);
        if (ev.stage) setReportStage(ev.stage);
        break;
      case 'question':
        submitting = false;
        askQuestion(ev);
        break;
      case 'progress':
        setStatus(ev.text);
        if (ev.total) {
          setProgress(ev.done / ev.total);
          setReportStage('analyze');
          setAnalyzeProgress(ev.done, ev.total);
        }
        break;
      case 'token': {
        setReportStage('write');
        const section = $('feedback-section');
        if (section.style.display !== 'block') {
          section.style.display = 'block';
          section.scrollIntoView({ behavior: 'smooth' });
          setStatus('Writing your feedback report…');
        }
        streamedHtml += ev.text;
        $('feedback-content').innerHTML = sanitize(streamedHtml);
        break;
      }
      case 'done':
        finishReportProgress();
        finishInterview(ev);
        break;
      case 'error':
        throw new Error(ev.message || 'Something went wrong');
    }
  }

  async function submitAnswer() {
    if (!running || submitting || !currentQuestion) return;
    submitting = true;
    nextBtn.disabled = true;
    clearInterval(countdownInterval);
    try { speechSynthesis.cancel(); } catch {}
    stopSTT(); stopSnapshots(); stopNudges();
    setStatus('Saving your answer…');
    window.Robot?.state('thinking');

    const form = new FormData();
    const blob = await stopRecording();
    if (blob) form.append('audio_file', blob, 'answer.webm');
    form.append('live_transcript', finalText.trim());
    form.append('camera_samples', JSON.stringify(cameraSamples));

    try {
      const r = await fetch(`/api/interviews/${CFG.id}/answer`, { method: 'POST', body: form });
      await readNdjson(r, handleEvent);
    } catch (e) {
      fail(e);
    }
  }

  function finishInterview(ev) {
    running = false;
    window.Robot?.state('idle', '');
    window.Robot?.say('Interview complete! Your feedback report is ready below.');
    submitting = false;
    currentQuestion = null;
    setProgress(1);
    startBtn.style.display = 'none';
    stopBtn.disabled = true;
    nextBtn.disabled = true;
    timerEl.textContent = '--';
    stopCamera();
    qmetaEl.innerHTML = '<strong>Interview complete 🎉</strong>';
    setStatus('Here is your feedback.');
    ['live_transcript_box', 'live_score_box', 'emotion_box', 'mic_box'].forEach(id => $(id)?.remove());

    const s = ev.summary || {};
    const cards = (ev.results || []).map(r => {
      const g = r.grade || {};
      const overall = g.overall != null ? `<span class="grade" style="float:right">${(+g.overall).toFixed(1)}/10</span>` : '';
      const fillers = r.fillers && Object.keys(r.fillers).length
        ? ` (${Object.entries(r.fillers).map(([w, n]) => `${escapeHtml(w)}×${n}`).join(', ')})` : '';
      return `<div class="answer-card">${overall}
        <strong>Q${r.number}${r.is_follow_up ? ' · follow-up' : ''}</strong> <span class="badge">${escapeHtml(r.difficulty || '')}</span><br/>
        ${escapeHtml(r.q)}<br/>
        <em>Your answer:</em> ${escapeHtml(r.transcript || '(no speech detected)')}<br/>
        ${g.improvement ? `💡 ${escapeHtml(g.improvement)}<br/>` : ''}
        <span class="rubric">Pace ${r.wpm ? Math.round(r.wpm) : '–'} WPM · Fillers ${r.filler_count || 0}${fillers} ·
          Eye contact ${r.gaze_pct ?? 0}% · ${escapeHtml(r.audio_status || '')}</span>
      </div>`;
    }).join('');
    $('summary_box').innerHTML = `
      <div class="rubric" style="margin-bottom:10px">Overall <strong style="font-size:20px;color:var(--primary)">${s.overall_score ?? '–'}/10</strong>
        · ${s.questions_answered || 0} answers (${s.follow_ups || 0} follow-ups) · ${s.total_fillers || 0} filler words
        · eye contact ${s.avg_gaze_pct != null ? Math.round(s.avg_gaze_pct) : '–'}%</div>${cards}<h3>Coach's report</h3>`;
    $('feedback-content').innerHTML = sanitize(ev.feedback || 'No feedback generated.');
    $('feedback-section').style.display = 'block';
    $('reportLink').href = ev.report_url;
    $('pdfLink').href = `${ev.report_url}.pdf`;
    $('report-links').style.display = 'flex';
    if (window.openFeedback) $('rate-interview').hidden = false;
    $('feedback-section').scrollIntoView({ behavior: 'smooth' });
  }

  function fail(e) {
    console.error(e);
    hideReportProgress();
    window.Robot?.state('idle', 'Something went wrong. Click Resume.');
    running = false;
    submitting = false;
    stopSTT(); stopSnapshots(); stopNudges(); stopCamera();
    setStatus(`Something went wrong: ${e.message}. Click Resume to continue where you left off.`);
    startBtn.disabled = false;
    startBtn.textContent = 'Resume';
    stopBtn.disabled = true;
    nextBtn.disabled = true;
  }

  async function startInterview() {
    if (running) return;
    running = true;
    startBtn.disabled = true;
    stopBtn.disabled = false;
    nextBtn.disabled = true;
    setStatus('Preparing interview…');
    streamedHtml = '';
    await initBrowserCamera();

    try {
      const st = await fetch(`/api/interviews/${CFG.id}`).then(r => r.json());
      if (st.done) { window.location = `/interviews/${CFG.id}/report`; return; }
      if (st.pending_question) { askQuestion(st.pending_question); return; }
      const r = await fetch(`/api/interviews/${CFG.id}/start`, { method: 'POST' });
      await readNdjson(r, handleEvent);
    } catch (e) {
      fail(e);
    }
  }

  async function pauseInterview() {
    running = false;
    window.Robot?.state('idle', '⏸ Paused');
    currentQuestion = null;
    clearInterval(countdownInterval);
    stopSTT(); stopSnapshots(); stopNudges();
    await stopRecording();
    stopCamera();
    try { speechSynthesis.cancel(); } catch {}
    startBtn.disabled = false;
    startBtn.textContent = 'Resume';
    stopBtn.disabled = true;
    nextBtn.disabled = true;
    timerEl.textContent = '--';
    setStatus('Paused. The current question will be asked again when you resume (also from the dashboard).');
  }

  setupMicPicker();
  navigator.mediaDevices?.addEventListener?.('devicechange', setupMicPicker);
  // Resuming an interview that already has answers: label the button accordingly.
  fetch(`/api/interviews/${CFG.id}`).then(r => r.json()).then(st => {
    if (st.started && !running) startBtn.textContent = 'Resume interview';
  }).catch(() => {});

  // Leaving the page (link click, tab close): close the webcam stream right away.
  window.addEventListener('pagehide', () => { stopCamera(); stopSTT(); stopMeter(); });

  startBtn.addEventListener('click', startInterview);
  stopBtn.addEventListener('click', pauseInterview);
  nextBtn.addEventListener('click', submitAnswer);
})();
