// "Ask about my feedback" chat on the interview report page.
(() => {
  const CFG = window.REPORT_CHAT;
  const log = document.getElementById('chatLog');
  const form = document.getElementById('chatForm');
  const input = document.getElementById('chatInput');
  const send = document.getElementById('chatSend');
  const suggest = document.getElementById('chatSuggest');
  if (!CFG || !form) return;
  const url = `/api/interviews/${CFG.id}/chat`;
  let busy = false;

  const esc = (s) => s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // The coach replies in plain text with "- " bullets and **bold**; turn that into safe HTML.
  function format(text) {
    const html = [];
    let list = null;
    for (const raw of esc(text).split('\n')) {
      const line = raw.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
      const bullet = line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
      if (bullet) { (list ||= []).push(`<li>${bullet[1]}</li>`); continue; }
      if (list) { html.push(`<ul>${list.join('')}</ul>`); list = null; }
      if (line.trim()) html.push(`<p>${line}</p>`);
    }
    if (list) html.push(`<ul>${list.join('')}</ul>`);
    return html.join('');
  }

  function bubble(role, text) {
    const div = document.createElement('div');
    div.className = `msg ${role}`;
    if (role === 'coach') div.innerHTML = `<div class="who">Coach</div><div class="body">${format(text)}</div>`;
    else div.textContent = text;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div;
  }

  function setBusy(on) {
    busy = on;
    send.disabled = on;
    send.textContent = on ? '…' : 'Send';
    suggest.querySelectorAll('button').forEach((b) => { b.disabled = on; });
  }

  const autosize = () => { input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 160)}px`; };
  input.addEventListener('input', autosize);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
  });
  suggest.querySelectorAll('button').forEach((b) => b.addEventListener('click', () => ask(b.textContent.trim())));
  form.addEventListener('submit', (e) => { e.preventDefault(); ask(input.value.trim()); });

  async function ask(question) {
    if (!question || busy) return;
    setBusy(true);
    input.value = ''; autosize();
    suggest.hidden = true;
    bubble('user', question);
    const reply = bubble('coach', '');
    const body = reply.querySelector('.body');
    body.innerHTML = '<span class="typing" aria-label="Coach is typing"><i></i><i></i><i></i></span>';
    let text = '';
    try {
      const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                   body: JSON.stringify({ message: question }) });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        const detail = typeof j.detail === 'string' ? j.detail : null;
        throw new Error(detail || (r.status === 409 ? 'Still answering your last question, one moment.' : `Request failed (${r.status}).`));
      }
      const reader = r.body.getReader();
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
          if (!line) continue;
          const ev = JSON.parse(line);
          if (ev.type === 'token') {
            text += ev.text;
            body.innerHTML = format(text);
            log.scrollTop = log.scrollHeight;
          } else if (ev.type === 'error') {
            const err = new Error(ev.message || 'Something went wrong.');
            err.needKey = !!ev.need_api_key;
            throw err;
          }
        }
      }
      if (!text) body.innerHTML = format("Sorry, I couldn't come up with an answer. Please try rephrasing.");
    } catch (e) {
      reply.remove();
      const div = bubble('error', '');
      div.innerHTML = e.needKey
        ? `${esc(e.message)} <a href="/settings/api-keys">Open API keys →</a>`
        : `${esc(e.message)} Your question is back in the box, so you can try again.`;
      input.value = question; autosize();
    } finally {
      setBusy(false);
      input.focus({ preventScroll: true });
    }
  }

  // Earlier conversation about this interview.
  fetch(url).then((r) => (r.ok ? r.json() : null)).then((j) => {
    if (!j?.messages?.length) return;
    j.messages.forEach((m) => bubble(m.role === 'user' ? 'user' : 'coach', m.content));
    suggest.hidden = true;
  }).catch(() => {});
})();
