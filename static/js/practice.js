(() => {
  const CFG = window.PRACTICE;
  const $ = (id) => document.getElementById(id);
  const chat = $('chat'), statusEl = $('status'), meterEl = $('meter'), turnsEl = $('turns');
  const startBtn = $('startBtn'), doneBtn = $('doneBtn'), repeatBtn = $('repeatBtn'), endBtn = $('endBtn');
  const handsFree = $('handsFree'), showFixes = $('showFixes');
  const micSelect = $('micSelect'), voiceSelect = $('voiceSelect'), rateInput = $('rate');

  // Turn-taking: after you start speaking, this much silence ends your turn (hands-free mode).
  // Set with the "Wait before replying" slider (shared with Talk, default 6 s).
  const endOfTurnSilenceMs = () => window.VoiceCore.pauseMs();
  const MIN_SPEECH_MS = 500;
  const MAX_TURN_MS = 120000;
  const VOICE_LEVEL = 10; // meter % that counts as speech

  let phase = 'idle';           // idle | ai | listening | thinking | done
  let lastAi = null;            // last AI payload {text, turn, max_turns, feedback}
  let pendingBubble = null;     // user bubble waiting for its transcript
  const userBubbles = {};       // turn index -> bubble element (to attach corrections)

  // ------------------------------------------------------------ helpers
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const store = { get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
                  set: (k, v) => { try { localStorage.setItem(k, v); } catch {} } };

  function setStatus(text, cls = '') { statusEl.textContent = text; statusEl.className = 'status ' + cls; }
  function setPhase(p) {
    phase = p;
    window.Robot?.state({ listening: 'listening', thinking: 'thinking' }[p] || 'idle');
    doneBtn.disabled = p !== 'listening';
    repeatBtn.disabled = !lastAi || p === 'thinking' || p === 'done';
    endBtn.disabled = p === 'idle' || p === 'thinking' || p === 'done';
  }

  function addBubble(role, text, extraClass = '') {
    $('intro')?.remove();
    const div = document.createElement('div');
    div.className = `msg ${role} ${extraClass}`;
    div.innerHTML = `<div class="who">${role === 'ai' ? 'AI' : 'You'}</div><div class="text">${esc(text)}</div>`;
    chat.appendChild(div);
    chat.scrollTop = chat.scrollHeight;
    return div;
  }

  function attachFeedback(fb) {
    if (!fb || !fb.index || !showFixes.checked) return;
    const bubble = userBubbles[fb.index];
    if (!bubble || bubble.querySelector('.fix') || (!fb.correction && !fb.better_way)) return;
    let html = '';
    if (fb.correction) {
      html += `✏️ <span class="bad">${esc(fb.correction.original)}</span> → <span class="good">${esc(fb.correction.corrected)}</span>
               <div class="muted">${esc(fb.correction.explanation)}</div>`;
    }
    if (fb.better_way) html += `<div>💬 More natural: <em>${esc(fb.better_way)}</em></div>`;
    const div = document.createElement('div');
    div.className = 'fix';
    div.innerHTML = html;
    bubble.appendChild(div);
  }

  function updateTurns(n) { turnsEl.textContent = `Turn ${Math.min(n, CFG.maxTurns)} of ${CFG.maxTurns}`; }

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

  // ------------------------------------------------------------ AI voice
  let utterance = null; // keep a reference (Chromium can GC it and never fire onend)

  function loadVoices() {
    const voices = speechSynthesis.getVoices().filter(v => /^en[-_]/i.test(v.lang));
    if (!voices.length) return;
    const saved = store.get('aic.voice');
    // Prefer Edge's neural "Natural" voices, then any online voice.
    const rank = (v) => (/natural/i.test(v.name) ? 0 : /online|google/i.test(v.name) ? 1 : 2);
    voices.sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
    voiceSelect.innerHTML = voices.map(v => `<option value="${esc(v.name)}">${esc(v.name.replace(/^Microsoft /, ''))} (${esc(v.lang)})</option>`).join('');
    voiceSelect.value = voices.some(v => v.name === saved) ? saved : voices[0].name;
  }
  if ('speechSynthesis' in window) {
    loadVoices();
    speechSynthesis.addEventListener?.('voiceschanged', loadVoices);
  }
  voiceSelect.addEventListener('change', () => store.set('aic.voice', voiceSelect.value));

  function speak(text, onend) {
    let finished = false;
    const finish = () => { if (!finished) { finished = true; onend && onend(); } };
    if (!('speechSynthesis' in window)) { finish(); return; }
    const u = new SpeechSynthesisUtterance(text);
    utterance = u;
    const voice = speechSynthesis.getVoices().find(v => v.name === voiceSelect.value);
    if (voice) u.voice = voice;
    u.lang = voice?.lang || 'en-US';
    u.rate = parseFloat(rateInput.value) || 1;
    u.onend = finish;
    u.onerror = finish;
    try { speechSynthesis.cancel(); } catch {}
    speechSynthesis.speak(u);
    setTimeout(finish, text.split(/\s+/).length * 600 / u.rate + 2500); // fallback if onend never fires
  }

  // ------------------------------------------------------------ microphone
  const VIRTUAL_MIC = /voicemeeter|vb-audio|cable output|virtual|stereo mix|what u hear|loopback/i;

  async function setupMicPicker() {
    if (!navigator.mediaDevices?.enumerateDevices) return;
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
    const saved = store.get('aic.micDeviceId');
    const pick = list.find(d => d.deviceId === saved) || list.find(d => !VIRTUAL_MIC.test(d.label)) || list[0];
    micSelect.innerHTML = list.map(d =>
      `<option value="${esc(d.deviceId)}">${esc(d.label || 'Microphone')}${VIRTUAL_MIC.test(d.label) ? ' (virtual)' : ''}</option>`).join('');
    if (pick) micSelect.value = pick.deviceId;
  }
  micSelect.addEventListener('change', () => store.set('aic.micDeviceId', micSelect.value));

  // ------------------------------------------------------------ listening (record + detect end of turn)
  let stream = null, recorder = null, chunks = [], audioCtx = null, raf = null, recognition = null, liveText = '';

  function startRecognition() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    liveText = '';
    if (!SR) return;
    try {
      recognition = new SR();
      recognition.lang = 'en-US';
      recognition.continuous = true;
      recognition.interimResults = false;
      recognition.onresult = (e) => {
        for (let i = e.resultIndex; i < e.results.length; i++) if (e.results[i].isFinal) liveText += e.results[i][0].transcript + ' ';
      };
      recognition.onerror = () => {};
      recognition.start();
    } catch { recognition = null; }
  }

  async function startListening() {
    if (phase === 'done') return;
    chunks = [];
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: micSelect.value ? { deviceId: { exact: micSelect.value } } : true,
      });
    } catch (e) {
      setStatus(`Microphone unavailable: ${e.message}`, 'listening');
      setPhase('idle');
      return;
    }
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    recorder.start(250);
    startRecognition();
    setPhase('listening');
    setStatus('🎙️ Your turn: speak now…', 'listening');

    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    audioCtx.resume();
    const analyser = audioCtx.createAnalyser();
    analyser.fftSize = 512;
    audioCtx.createMediaStreamSource(stream).connect(analyser);
    const buf = new Uint8Array(analyser.fftSize);
    const started = performance.now();
    let speechStart = 0, lastVoice = 0;

    const tick = () => {
      if (phase !== 'listening') return;
      analyser.getByteTimeDomainData(buf);
      let peak = 0;
      for (const v of buf) peak = Math.max(peak, Math.abs(v - 128));
      const pct = Math.min(100, Math.round((peak / 128) * 250));
      meterEl.style.width = pct + '%';
      window.Robot?.level(pct);
      const now = performance.now();
      if (pct > VOICE_LEVEL) {
        if (!speechStart) { speechStart = now; setStatus('👂 Listening…', 'listening'); }
        lastVoice = now;
      }
      const spokeEnough = speechStart && lastVoice - speechStart > MIN_SPEECH_MS;
      if (handsFree.checked && spokeEnough && now - lastVoice > endOfTurnSilenceMs()) { sendTurn(); return; }
      if (now - started > MAX_TURN_MS) { sendTurn(); return; }
      if (!speechStart && now - started > 8000) {
        setStatus('🎙️ Still listening… (check the microphone list if the bar doesn\'t move)', 'listening');
      }
      raf = requestAnimationFrame(tick);
    };
    tick();
  }

  async function stopListening() {
    if (raf) cancelAnimationFrame(raf);
    raf = null;
    meterEl.style.width = '0%';
    if (recognition) { try { recognition.stop(); } catch {} recognition = null; }
    if (recorder && recorder.state !== 'inactive') {
      await new Promise(res => { recorder.onstop = res; recorder.stop(); });
    }
    stream?.getTracks().forEach(t => t.stop());
    stream = null; recorder = null;
    return chunks.length ? new Blob(chunks, { type: 'audio/webm' }) : null;
  }

  // ------------------------------------------------------------ conversation flow
  async function handleEvent(ev) {
    switch (ev.type) {
      case 'history':
        for (const m of ev.messages) {
          const b = addBubble(m.role, m.text);
          if (m.role === 'user') {
            const idx = Object.keys(userBubbles).length + 1;
            userBubbles[idx] = b;
          }
        }
        for (const t of ev.turns || []) attachFeedback(t);
        updateTurns((ev.turns || []).length);
        break;
      case 'status':
        setStatus(ev.text);
        break;
      case 'heard':
        if (pendingBubble) {
          pendingBubble.classList.remove('pending');
          pendingBubble.querySelector('.text').textContent = ev.text || '(I didn\'t catch that)';
          if (ev.text) userBubbles[lastAi?.turn] = pendingBubble;
          pendingBubble = null;
        }
        setStatus('💭 Thinking…');
        break;
      case 'ai':
        lastAi = ev;
        attachFeedback(ev.feedback);
        updateTurns(ev.turn - 1);
        addBubble('ai', ev.text);
        setPhase('ai');
        setStatus('🔊 AI is speaking…');
        speak(ev.text, () => { if (phase === 'ai') startListening(); });
        break;
      case 'done':
        finish(ev);
        break;
      case 'error':
        throw new Error(ev.message || 'Something went wrong');
    }
  }

  async function sendTurn(end = false) {
    if (phase !== 'listening' && !end) return;
    setPhase('thinking');
    const blob = await stopListening();
    const form = new FormData();
    if (end) {
      form.append('end', 'true');
      setStatus('Wrapping up…');
    } else {
      if (blob) form.append('audio_file', blob, 'turn.webm');
      form.append('live_transcript', liveText.trim());
      pendingBubble = addBubble('user', '…', 'pending');
      setStatus('📝 Transcribing…');
    }
    try {
      const r = await fetch(`/api/practice/${CFG.id}/turn`, { method: 'POST', body: form });
      await readNdjson(r, handleEvent);
    } catch (e) {
      console.error(e);
      setStatus(`Something went wrong: ${e.message}`);
      setPhase('idle');
      startBtn.disabled = false;
      startBtn.textContent = '▶ Continue';
    }
  }

  function finish(ev) {
    setPhase('done');
    startBtn.disabled = true;
    try { speechSynthesis.cancel(); } catch {}
    const showReport = () => renderReport(ev);
    if (ev.final_ai_text) {
      addBubble('ai', ev.final_ai_text);
      setStatus('🔊 AI is speaking…');
      speak(ev.final_ai_text, showReport);
    } else {
      showReport();
    }
  }

  function renderReport(ev) {
    setStatus('Conversation finished 🎉');
    updateTurns((ev.turns || []).length);
    const r = ev.report || {};
    const s = r.stats || {};
    const list = (xs) => (xs || []).map(x => `<li>${esc(x)}</li>`).join('');
    const box = $('report');
    box.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
        <h2 style="margin:0">📋 Your speaking report</h2>
        <a class="btn small" href="${ev.report_url}">Open full report</a>
      </div>
      <p style="font-size:18px;margin:12px 0 4px"><strong>${r.overall_score != null ? (+r.overall_score).toFixed(1) + '/10' : '–'}</strong>
        ${r.cefr_level ? ` · estimated level <strong>${esc(r.cefr_level)}</strong>` : ''}</p>
      <p class="muted" style="margin-top:0">${s.turns || 0} turns · ${s.words || 0} words · ${s.avg_wpm ? Math.round(s.avg_wpm) + ' words/min · ' : ''}${s.filler_count || 0} filler words · ${s.corrections || 0} corrections</p>
      <p>${esc(r.summary || '')}</p>
      ${r.strengths ? `<h3>👍 Strengths</h3><ul>${list(r.strengths)}</ul>` : ''}
      ${r.improvements ? `<h3>💡 Tips</h3><ul>${list(r.improvements)}</ul>` : ''}
      ${r.useful_phrases ? `<h3>🧩 Useful phrases</h3><ul>${list(r.useful_phrases)}</ul>` : ''}`;
    box.style.display = 'block';
    box.scrollIntoView({ behavior: 'smooth' });
  }

  async function start() {
    startBtn.disabled = true;
    setStatus('Starting…');
    await setupMicPicker();
    try {
      const r = await fetch(`/api/practice/${CFG.id}/start`, { method: 'POST' });
      await readNdjson(r, handleEvent);
    } catch (e) {
      setStatus(`Could not start: ${e.message}`);
      startBtn.disabled = false;
    }
  }

  startBtn.addEventListener('click', () => {
    if (lastAi && phase === 'idle') { // continue after an error
      startBtn.disabled = true;
      startListening();
    } else {
      start();
    }
  });
  doneBtn.addEventListener('click', () => sendTurn());
  repeatBtn.addEventListener('click', async () => {
    if (!lastAi) return;
    if (phase === 'listening') await stopListening();
    setPhase('ai');
    setStatus('🔊 AI is speaking…');
    speak(lastAi.text, () => { if (phase === 'ai') startListening(); });
  });
  endBtn.addEventListener('click', async () => {
    if (!confirm('End the conversation and get your feedback?')) return;
    try { speechSynthesis.cancel(); } catch {}
    sendTurn(true);
  });
  showFixes.addEventListener('change', () => {
    chat.querySelectorAll('.fix').forEach(el => { el.style.display = showFixes.checked ? '' : 'none'; });
  });
  window.addEventListener('pagehide', () => { stopListening(); try { speechSynthesis.cancel(); } catch {} });

  window.VoiceCore.setupPauseSlider($('pause'), $('pauseLabel'));
  setupMicPicker();
  navigator.mediaDevices?.addEventListener?.('devicechange', setupMicPicker);
})();
