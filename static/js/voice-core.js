// Shared voice helpers: AI voice (speech synthesis), microphone picker, and a listener that
// records the user and detects when they stop talking (voice activity detection).
window.VoiceCore = (() => {
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const store = {
    get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch {} },
  };

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

  // ---------------------------------------------------------- voice output
  function setupVoices(select) {
    const load = () => {
      const voices = speechSynthesis.getVoices().filter(v => /^en[-_]/i.test(v.lang));
      if (!voices.length) return;
      const saved = store.get('aic.voice');
      const rank = (v) => (/natural/i.test(v.name) ? 0 : /online|google/i.test(v.name) ? 1 : 2);
      voices.sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
      select.innerHTML = voices.map(v => `<option value="${esc(v.name)}">${esc(v.name.replace(/^Microsoft /, ''))} (${esc(v.lang)})</option>`).join('');
      select.value = voices.some(v => v.name === saved) ? saved : voices[0].name;
    };
    if ('speechSynthesis' in window) {
      load();
      speechSynthesis.addEventListener?.('voiceschanged', load);
    }
    select.addEventListener('change', () => store.set('aic.voice', select.value));
  }

  let currentUtterance = null; // keep a reference: Chromium can GC it and never fire onend
  function speak(text, { voiceSelect, rate = 1 } = {}) {
    return new Promise((resolve) => {
      let done = false;
      const finish = () => { if (!done) { done = true; resolve(); } };
      if (!('speechSynthesis' in window)) return finish();
      const u = new SpeechSynthesisUtterance(text);
      currentUtterance = u;
      const voice = speechSynthesis.getVoices().find(v => v.name === voiceSelect?.value);
      if (voice) u.voice = voice;
      u.lang = voice?.lang || 'en-US';
      u.rate = rate;
      u.onend = finish;
      u.onerror = finish;
      try { speechSynthesis.cancel(); } catch {}
      speechSynthesis.speak(u);
      setTimeout(finish, text.split(/\s+/).length * 600 / rate + 2500); // if onend never fires
    });
  }
  const stopSpeaking = () => { try { speechSynthesis.cancel(); } catch {} };

  // ---------------------------------------------------------- microphone
  const VIRTUAL_MIC = /voicemeeter|vb-audio|cable output|virtual|stereo mix|what u hear|loopback/i;

  async function setupMicPicker(select) {
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
    select.innerHTML = list.map(d =>
      `<option value="${esc(d.deviceId)}">${esc(d.label || 'Microphone')}${VIRTUAL_MIC.test(d.label) ? ' (virtual)' : ''}</option>`).join('');
    if (pick) select.value = pick.deviceId;
    if (!select.dataset.bound) {
      select.dataset.bound = '1';
      select.addEventListener('change', () => store.set('aic.micDeviceId', select.value));
    }
  }

  // ---------------------------------------------------------- "wait before replying" setting
  // How long the user may pause before their turn is sent automatically (seconds, remembered).
  const PAUSE_KEY = 'aic.pauseSec';
  const DEFAULT_PAUSE_SEC = 6;
  function pauseMs() {
    const v = parseFloat(store.get(PAUSE_KEY));
    return (v >= 1 && v <= 15 ? v : DEFAULT_PAUSE_SEC) * 1000;
  }
  function setupPauseSlider(range, label) {
    if (!range) return;
    range.value = pauseMs() / 1000;
    const show = () => { if (label) label.textContent = `${range.value} s`; };
    show();
    range.addEventListener('input', () => { store.set(PAUSE_KEY, range.value); show(); });
  }

  // ---------------------------------------------------------- listener (record + voice activity detection)
  let audioCtx = null;

  /**
   * opts: micSelect, meterEl, voiceLevel (meter % that counts as speech), autoEnd() -> bool,
   *       silenceMs (silence that ends a turn), minSpeechMs, maxMs, noSpeechMs,
   *       onSpeechStart(), onEnd(reason: 'silence'|'max'), onNoSpeech()
   */
  function createListener(opts) {
    const o = { voiceLevel: 10, silenceMs: pauseMs(), minSpeechMs: 500, maxMs: 120000, noSpeechMs: 0, ...opts };
    let stream = null, recorder = null, chunks = [], raf = null, recognition = null, active = false;
    const state = { liveText: '', spoke: false };

    function startRecognition() {
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SR) return;
      try {
        recognition = new SR();
        recognition.lang = 'en-US';
        recognition.continuous = true;
        recognition.interimResults = false;
        recognition.onresult = (e) => {
          for (let i = e.resultIndex; i < e.results.length; i++) if (e.results[i].isFinal) state.liveText += e.results[i][0].transcript + ' ';
        };
        recognition.onerror = () => {};
        recognition.start();
      } catch { recognition = null; }
    }

    async function start() {
      chunks = []; state.liveText = ''; state.spoke = false;
      stream = await navigator.mediaDevices.getUserMedia({
        audio: o.micSelect?.value ? { deviceId: { exact: o.micSelect.value } } : true,
      });
      recorder = new MediaRecorder(stream);
      recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
      recorder.start(250);
      startRecognition();
      active = true;

      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      audioCtx.resume();
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 512;
      audioCtx.createMediaStreamSource(stream).connect(analyser);
      const buf = new Uint8Array(analyser.fftSize);
      const started = performance.now();
      let speechStart = 0, lastVoice = 0, noSpeechFired = false;

      const tick = () => {
        if (!active) return;
        analyser.getByteTimeDomainData(buf);
        let peak = 0;
        for (const v of buf) peak = Math.max(peak, Math.abs(v - 128));
        const pct = Math.min(100, Math.round((peak / 128) * 250));
        if (o.meterEl) o.meterEl.style.width = pct + '%';
        window.Robot?.level(pct);
        const now = performance.now();
        if (pct > o.voiceLevel) {
          if (!speechStart) { speechStart = now; o.onSpeechStart?.(); }
          lastVoice = now;
        }
        state.spoke = !!speechStart && lastVoice - speechStart > o.minSpeechMs;
        if (o.autoEnd?.() !== false && state.spoke && now - lastVoice > o.silenceMs) { active = false; o.onEnd?.('silence'); return; }
        if (now - started > o.maxMs) { active = false; o.onEnd?.('max'); return; }
        if (o.noSpeechMs && !speechStart && !noSpeechFired && now - started > o.noSpeechMs) { noSpeechFired = true; o.onNoSpeech?.(); }
        raf = requestAnimationFrame(tick);
      };
      tick();
    }

    async function stop() {
      active = false;
      if (raf) cancelAnimationFrame(raf);
      raf = null;
      if (o.meterEl) o.meterEl.style.width = '0%';
      if (recognition) { try { recognition.stop(); } catch {} recognition = null; }
      if (recorder && recorder.state !== 'inactive') {
        await new Promise(res => { recorder.onstop = res; recorder.stop(); });
      }
      stream?.getTracks().forEach(t => t.stop());
      stream = null; recorder = null;
      return chunks.length ? new Blob(chunks, { type: 'audio/webm' }) : null;
    }

    return { start, stop, state, get active() { return active; } };
  }

  return { esc, store, readNdjson, setupVoices, speak, stopSpeaking, setupMicPicker, createListener,
           pauseMs, setupPauseSlider };
})();
