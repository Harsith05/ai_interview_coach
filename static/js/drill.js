(() => {
  const { esc, readNdjson, setupVoices, speak, stopSpeaking, setupMicPicker, createListener,
          pauseMs, setupPauseSlider } = window.VoiceCore;
  const CFG = window.DRILL;
  const $ = (id) => document.getElementById(id);
  const qnum = $('qnum'), qtext = $('qtext'), stateEl = $('state'), heardEl = $('heard'), meterEl = $('meter');
  const progressEl = $('progress'), fbCard = $('fb'), historyEl = $('history');
  const startBtn = $('startBtn'), doneBtn = $('doneBtn'), repeatBtn = $('repeatBtn'), skipBtn = $('skipBtn'), endBtn = $('endBtn');
  const handsFree = $('handsFree'), micSelect = $('micSelect'), voiceSelect = $('voiceSelect'), rateInput = $('rate');

  let phase = 'idle';       // idle | speaking | listening | thinking | done
  let current = null;       // current question payload
  let listener = null;
  const shown = new Set();  // question numbers whose feedback is already on screen

  const rate = () => parseFloat(rateInput.value) || 1;

  function setPhase(p, text) {
    phase = p;
    window.Robot?.state({ listening: 'listening', thinking: 'thinking' }[p] || 'idle');
    const texts = { idle: '', speaking: '🔊 Reading the question…  (Space to answer now)', listening: '🎙️ Your answer…',
                    thinking: '💭 One moment…', done: 'Finished 🎉' };
    stateEl.textContent = text ?? texts[p];
    stateEl.className = 'state ' + (p === 'listening' ? 'listening' : '');
    startBtn.disabled = p !== 'idle';
    doneBtn.disabled = p !== 'listening';
    repeatBtn.disabled = !current || ['thinking', 'done'].includes(p);
    skipBtn.disabled = !current || ['thinking', 'done'].includes(p);
    endBtn.disabled = !current || ['thinking', 'done'].includes(p);
  }

  function renderFeedback(f) {
    if (!f || f.skipped || shown.has(f.number)) return;
    shown.add(f.number);
    // history line
    $('historyCard').style.display = 'block';
    const item = document.createElement('div');
    item.className = 'item';
    item.innerHTML = `<strong>Q${f.number}. ${esc(f.question)}</strong>${f.score != null ? ` <span class="badge green">${(+f.score).toFixed(1)}/10</span>` : ''}
      <div class="muted">${esc(f.answer || '')}</div>`;
    historyEl.prepend(item);
    // detailed card (only when there is real feedback)
    if (f.score == null) return;
    const list = (xs) => (xs || []).map(x => `<li>${esc(x)}</li>`).join('');
    fbCard.innerHTML = `
      <div style="display:flex;justify-content:space-between;gap:10px;align-items:baseline">
        <strong>Feedback on Q${f.number}</strong><span class="score">${(+f.score).toFixed(1)}/10</span></div>
      <p>💬 ${esc(f.spoken_feedback || '')}</p>
      ${f.missed?.length ? `<p style="margin-bottom:0"><strong>Missed:</strong></p><ul>${list(f.missed)}</ul>` : ''}
      ${f.language_tip ? `<p>✏️ ${esc(f.language_tip)}</p>` : ''}
      ${f.better_answer ? `<details><summary style="cursor:pointer">A stronger answer</summary><p>${esc(f.better_answer)}</p></details>` : ''}`;
    fbCard.style.display = 'block';
  }

  async function listen() {
    await listener?.stop();
    listener = createListener({
      micSelect, meterEl,
      silenceMs: pauseMs(),
      maxMs: 180000,
      autoEnd: () => handsFree.checked,
      onSpeechStart: () => setPhase('listening', '👂 Listening… (pause to finish)'),
      onEnd: () => send(),
    });
    try {
      await listener.start();
      setPhase('listening');
    } catch (e) {
      setPhase('idle', `Microphone unavailable: ${e.message}`);
    }
  }

  async function handleEvent(ev) {
    switch (ev.type) {
      case 'status':
        stateEl.textContent = ev.text;
        break;
      case 'history':
        for (const r of ev.results || []) renderFeedback(r);
        break;
      case 'heard':
        heardEl.textContent = ev.text ? `You said: "${ev.text}"` : '';
        break;
      case 'question':
        current = ev;
        renderFeedback(ev.feedback);
        qnum.textContent = `Question ${ev.number} of ${ev.total}`;
        qtext.textContent = ev.question;
        progressEl.style.width = `${((ev.number - 1) / ev.total) * 100}%`;
        setPhase('speaking');
        await speak(ev.speech, { voiceSelect, rate: rate() });
        if (phase === 'speaking') { heardEl.textContent = ''; listen(); }
        break;
      case 'done':
        await finish(ev);
        break;
      case 'error':
        throw new Error(ev.message || 'Something went wrong');
    }
  }

  async function post(form) {
    try {
      const r = await fetch(`/api/drill/${CFG.id}/turn`, { method: 'POST', body: form });
      await readNdjson(r, handleEvent);
    } catch (e) {
      console.error(e);
      setPhase('idle', `Something went wrong: ${e.message}`);
      startBtn.textContent = '▶ Continue';
    }
  }

  async function send() {
    if (phase !== 'listening') return;
    setPhase('thinking', '📝 Transcribing your answer…');
    const blob = await listener?.stop();
    const form = new FormData();
    if (blob) form.append('audio_file', blob, 'answer.webm');
    form.append('live_transcript', (listener?.state.liveText || '').trim());
    await post(form);
  }

  async function command(name) {
    stopSpeaking();
    await listener?.stop();
    setPhase('thinking', name === 'end' ? 'Finishing up…' : 'Skipping…');
    const form = new FormData();
    form.append(name, 'true');
    await post(form);
  }

  async function finish(ev) {
    setPhase('done');
    progressEl.style.width = '100%';
    for (const r of ev.results || []) renderFeedback(r);
    const r = ev.report || {};
    qnum.textContent = 'All done';
    qtext.textContent = r.overall_score != null ? `Average score: ${r.overall_score}/10` : 'Practice finished';
    const box = $('report');
    box.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
        <h2 style="margin:0">Results</h2><a class="btn small" href="${ev.report_url}">Open full results</a></div>
      <p>${r.answered || 0} of ${r.total || 0} answered${r.skipped ? ` · ${r.skipped} skipped` : ''}${r.avg_wpm ? ` · ${r.avg_wpm} words/min` : ''} · ${r.filler_count || 0} filler words</p>
      ${r.review?.length ? `<p>Worth practising again: question${r.review.length > 1 ? 's' : ''} ${r.review.join(', ')}.</p>` : ''}`;
    box.style.display = 'block';
    box.scrollIntoView({ behavior: 'smooth' });
    if (r.closing) await speak(r.closing, { voiceSelect, rate: rate() });
  }

  async function start() {
    setPhase('thinking', 'Starting…');
    await setupMicPicker(micSelect);
    try {
      const r = await fetch(`/api/drill/${CFG.id}/start`, { method: 'POST' });
      await readNdjson(r, handleEvent);
    } catch (e) {
      setPhase('idle', `Could not start: ${e.message}`);
    }
  }

  async function primary() {
    if (phase === 'idle') return current ? listen() : start();
    if (phase === 'speaking') { stopSpeaking(); heardEl.textContent = ''; return listen(); }
    if (phase === 'listening') return send();
  }

  startBtn.addEventListener('click', primary);
  doneBtn.addEventListener('click', () => send());
  repeatBtn.addEventListener('click', async () => {
    if (!current) return;
    await listener?.stop();
    setPhase('speaking');
    await speak(current.question, { voiceSelect, rate: rate() });
    if (phase === 'speaking') listen();
  });
  skipBtn.addEventListener('click', () => command('skip'));
  endBtn.addEventListener('click', () => { if (confirm('Finish now and see your results?')) command('end'); });
  document.addEventListener('keydown', (e) => {
    if (e.code !== 'Space' || e.repeat || ['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON'].includes(document.activeElement?.tagName)) return;
    e.preventDefault();
    primary();
  });
  window.addEventListener('pagehide', () => { listener?.stop(); stopSpeaking(); });

  setupVoices(voiceSelect);
  setupPauseSlider($('pause'), $('pauseLabel'));
  setupMicPicker(micSelect);
  setPhase('idle');
})();
