(() => {
  const { esc, readNdjson, setupVoices, speak, stopSpeaking, setupMicPicker, createListener,
          pauseMs, setupPauseSlider } = window.VoiceCore;
  const CFG = window.TALK;
  const $ = (id) => document.getElementById(id);
  const orb = $('orb'), stateEl = $('state'), caption = $('caption'), chat = $('chat'), meterEl = $('meter');
  const talkBtn = $('talkBtn'), doneBtn = $('doneBtn'), repeatBtn = $('repeatBtn'), endBtn = $('endBtn');
  const handsFree = $('handsFree'), standbyOn = $('standbyOn'), showFixes = $('showFixes');
  const micSelect = $('micSelect'), voiceSelect = $('voiceSelect'), rateInput = $('rate');

  const STANDBY_AFTER_MS = 20000;   // quiet this long while listening -> standby

  let phase = 'idle';   // idle | speaking | listening | thinking | standby | done
  let started = false;  // conversation started on the server
  let lastAi = null;    // last AI payload
  let listener = null;
  let pendingBubble = null;
  const userBubbles = {};

  // ------------------------------------------------------------ UI helpers
  const STATE_TEXT = {
    idle: 'Click the orb (or press Space) to start',
    speaking: `🔊 ${CFG.name} is speaking…  (Space to interrupt)`,
    listening: '🎙️ Listening…',
    thinking: '💭 Thinking…',
    standby: `💤 Standby. Say "Hey ${CFG.name}" or press Space`,
    done: 'Conversation saved to memory. See you next time! 👋',
  };
  function setPhase(p, text) {
    phase = p;
    window.Robot?.state({ listening: 'listening', thinking: 'thinking', standby: 'sleep' }[p] || 'idle');
    orb.className = 'orb ' + (p === 'done' ? 'idle' : p);
    stateEl.textContent = text || STATE_TEXT[p];
    stateEl.className = 'state ' + (p === 'listening' ? 'listening' : '');
    talkBtn.textContent = p === 'idle' ? '🎙️ Start talking' : p === 'standby' ? '🎙️ Wake up' : p === 'speaking' ? '✋ Interrupt' : '🎙️ Talking…';
    talkBtn.disabled = p === 'thinking' || p === 'listening' || p === 'done';
    doneBtn.disabled = p !== 'listening';
    repeatBtn.disabled = !lastAi || ['thinking', 'done'].includes(p);
    endBtn.disabled = !started || ['thinking', 'done'].includes(p);
  }

  function addBubble(role, text, cls = '') {
    const div = document.createElement('div');
    div.className = `msg ${role} ${cls}`;
    div.innerHTML = `<div class="text">${esc(text)}</div>`;
    chat.appendChild(div);
    chat.scrollTop = chat.scrollHeight;
    return div;
  }

  function addNote(text) {
    const div = document.createElement('div');
    div.className = 'learned';
    div.textContent = text;
    chat.appendChild(div);
    chat.scrollTop = chat.scrollHeight;
  }

  function attachFeedback(fb) {
    if (!fb?.index) return;
    const bubble = userBubbles[fb.index];
    if (!bubble || bubble.querySelector('.fix') || (!fb.correction && !fb.better_way)) return;
    const div = document.createElement('div');
    div.className = 'fix';
    div.style.display = showFixes.checked ? '' : 'none';
    div.innerHTML = (fb.correction ? `✏️ <span class="bad">${esc(fb.correction.original)}</span> → <span class="good">${esc(fb.correction.corrected)}</span> <span class="muted">(${esc(fb.correction.explanation)})</span>` : '')
      + (fb.better_way ? `<div>💬 More natural: <em>${esc(fb.better_way)}</em></div>` : '');
    bubble.appendChild(div);
  }

  // ------------------------------------------------------------ listening
  async function listen(mode = 'normal') {
    if (phase === 'done') return;
    await listener?.stop();
    const standby = mode === 'standby';
    listener = createListener({
      micSelect, meterEl,
      // Standby only needs to catch "Hey <name>…", so it ends quickly; a real turn waits for
      // the user's chosen pause ("Wait before replying").
      silenceMs: standby ? 1500 : pauseMs(),
      maxMs: standby ? 12000 : 120000,
      noSpeechMs: standby ? 0 : (standbyOn.checked ? STANDBY_AFTER_MS : 0),
      autoEnd: () => standby || handsFree.checked,
      onSpeechStart: () => { if (!standby) setPhase('listening', '👂 Hearing you…'); },
      onEnd: (reason) => {
        if (standby && reason === 'max' && !listener.state.spoke) { listen('standby'); return; } // nothing said: keep waiting
        sendTurn({ standby });
      },
      onNoSpeech: () => { if (phase === 'listening') goStandby(); },
    });
    try {
      await listener.start();
    } catch (e) {
      setPhase('idle', `Microphone unavailable: ${e.message}`);
      return;
    }
    if (standby) setPhase('standby'); else setPhase('listening');
  }

  async function goStandby() {
    caption.textContent = '';
    await listen('standby');
  }

  // ------------------------------------------------------------ server events
  async function handleEvent(ev) {
    switch (ev.type) {
      case 'status':
        stateEl.textContent = ev.text;
        break;
      case 'history': {
        let idx = 0;
        for (const m of ev.messages) {
          const b = addBubble(m.role, m.text);
          if (m.role === 'user') userBubbles[++idx] = b;
        }
        for (const t of ev.turns || []) attachFeedback(t);
        break;
      }
      case 'ignored':  // standby: speech that wasn't addressed to the assistant
        pendingBubble?.remove(); pendingBubble = null;
        await goStandby();
        break;
      case 'woke':
        setPhase('thinking', `💭 Yes?`);
        break;
      case 'heard':
        if (pendingBubble) {
          pendingBubble.classList.remove('pending');
          pendingBubble.querySelector('.text').textContent = ev.text || "(didn't catch that)";
          if (ev.text) userBubbles[lastAi?.turn] = pendingBubble;
          pendingBubble = null;
        } else if (ev.text) {
          userBubbles[lastAi?.turn] = addBubble('user', ev.text);
        }
        setPhase('thinking');
        break;
      case 'ai':
        started = true;
        lastAi = ev;
        attachFeedback(ev.feedback);
        for (const f of ev.learned || []) addNote(`🧠 Noted: ${f}`);
        addBubble('ai', ev.text);
        caption.textContent = ev.text;
        setPhase('speaking');
        await speak(ev.text, { voiceSelect, rate: parseFloat(rateInput.value) || 1 });
        if (phase === 'speaking') listen();
        break;
      case 'done':
        if (ev.final_ai_text) {
          addBubble('ai', ev.final_ai_text);
          caption.textContent = ev.final_ai_text;
          setPhase('speaking');
          await speak(ev.final_ai_text, { voiceSelect, rate: parseFloat(rateInput.value) || 1 });
        }
        setPhase('done');
        talkBtn.disabled = false;
        talkBtn.textContent = '🔄 New conversation';
        talkBtn.onclick = () => document.querySelector('form[action="/talk/new"]').submit();
        break;
      case 'error':
        throw new Error(ev.message || 'Something went wrong');
    }
  }

  async function sendTurn({ standby = false, end = false } = {}) {
    if (!end && phase !== 'listening' && phase !== 'standby') return;
    const wasStandby = standby;
    if (!wasStandby) setPhase('thinking', end ? 'Saving what I learned today…' : '📝 Transcribing…');
    const blob = await listener?.stop();
    const live = listener?.state.liveText || '';
    const form = new FormData();
    if (end) {
      form.append('end', 'true');
    } else {
      if (blob) form.append('audio_file', blob, 'turn.webm');
      form.append('live_transcript', live.trim());
      if (wasStandby) form.append('standby', 'true');
      else pendingBubble = addBubble('user', '…', 'pending');
    }
    try {
      const r = await fetch(`/api/talk/${CFG.id}/turn`, { method: 'POST', body: form });
      await readNdjson(r, handleEvent);
    } catch (e) {
      console.error(e);
      pendingBubble?.remove(); pendingBubble = null;
      setPhase('idle', `Something went wrong: ${e.message}. Click to continue.`);
    }
  }

  async function start() {
    setPhase('thinking', 'Connecting…');
    await setupMicPicker(micSelect);
    try {
      const r = await fetch(`/api/talk/${CFG.id}/start`, { method: 'POST' });
      await readNdjson(r, handleEvent);
    } catch (e) {
      setPhase('idle', `Could not start: ${e.message}`);
    }
  }

  // One action for the orb, the main button and the Space key.
  async function primaryAction() {
    if (phase === 'idle') {
      if (!started) return start();
      return listen();                      // continue after an error
    }
    if (phase === 'standby') { await listener?.stop(); return listen(); }
    if (phase === 'speaking') { stopSpeaking(); return listen(); }   // interrupt
    if (phase === 'listening') return sendTurn();                    // done speaking
  }

  orb.addEventListener('click', primaryAction);
  talkBtn.addEventListener('click', () => { if (phase !== 'done') primaryAction(); });
  doneBtn.addEventListener('click', () => sendTurn());
  repeatBtn.addEventListener('click', async () => {
    if (!lastAi) return;
    await listener?.stop();
    setPhase('speaking');
    await speak(lastAi.text, { voiceSelect, rate: parseFloat(rateInput.value) || 1 });
    if (phase === 'speaking') listen();
  });
  endBtn.addEventListener('click', () => { stopSpeaking(); sendTurn({ end: true }); });
  showFixes.addEventListener('change', () => {
    chat.querySelectorAll('.fix').forEach(el => { el.style.display = showFixes.checked ? '' : 'none'; });
  });
  document.addEventListener('keydown', (e) => {
    if (e.code !== 'Space' || e.repeat || ['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON'].includes(document.activeElement?.tagName)) return;
    e.preventDefault();
    primaryAction();
  });
  window.addEventListener('pagehide', () => { listener?.stop(); stopSpeaking(); });

  setupVoices(voiceSelect);
  setupPauseSlider($('pause'), $('pauseLabel'));
  setupMicPicker(micSelect);
  navigator.mediaDevices?.addEventListener?.('devicechange', () => setupMicPicker(micSelect));
  setPhase('idle');
})();
