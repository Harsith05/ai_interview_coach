// "Send feedback" dialog, shared by every page (markup in base.html).
// Open it with window.openFeedback({ kind, rating, interviewId, title, intro }).
(() => {
  const dlg = document.getElementById('feedbackDialog');
  if (!dlg) return;
  const form = document.getElementById('feedbackForm');
  const msg = document.getElementById('fbMessage');
  const count = document.getElementById('fbCount');
  const err = document.getElementById('fbError');
  const sendBtn = document.getElementById('fbSend');
  const stars = [...dlg.querySelectorAll('.fb-star')];
  const title = document.getElementById('fbTitle');
  const intro = document.getElementById('fbIntro');
  const body = document.getElementById('fbBody');
  const done = document.getElementById('fbDone');
  let rating = null, interviewId = null, closeTimer = null;

  function paintStars(n) {
    stars.forEach((s, i) => {
      const on = n != null && i < n;
      s.classList.toggle('on', on);
      s.setAttribute('aria-checked', String(i + 1 === rating));
    });
  }
  stars.forEach((s, i) => {
    s.addEventListener('click', () => { rating = rating === i + 1 ? null : i + 1; paintStars(rating); });
    s.addEventListener('mouseenter', () => paintStars(i + 1));
    s.addEventListener('focus', () => paintStars(i + 1));
  });
  dlg.querySelector('.fb-stars').addEventListener('mouseleave', () => paintStars(rating));
  dlg.querySelector('.fb-stars').addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    e.preventDefault();
    rating = Math.min(5, Math.max(1, (rating || 0) + (e.key === 'ArrowRight' ? 1 : -1)));
    paintStars(rating); stars[rating - 1].focus();
  });

  msg.addEventListener('input', () => { count.textContent = `${msg.value.length}/2000`; err.textContent = ''; });

  window.openFeedback = (opts = {}) => {
    clearTimeout(closeTimer);
    form.reset();
    rating = opts.rating ?? null;
    interviewId = opts.interviewId ?? null;
    const kind = form.querySelector(`input[name="fbKind"][value="${opts.kind || 'other'}"]`);
    if (kind) kind.checked = true;
    title.textContent = opts.title || 'Send feedback';
    intro.textContent = opts.intro || 'Found a bug, have an idea, or just want to tell us what you think? We read every message.';
    paintStars(rating);
    count.textContent = '0/2000';
    err.textContent = '';
    body.hidden = false; done.hidden = true;
    sendBtn.disabled = false; sendBtn.textContent = 'Send feedback';
    document.querySelector('details.menu[open]')?.removeAttribute('open');
    dlg.showModal();
    msg.focus();
  };

  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });   // click on the backdrop
  dlg.querySelectorAll('[data-fb-close]').forEach(b => b.addEventListener('click', () => dlg.close()));

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const message = msg.value.trim();
    if (!message && rating == null) { err.textContent = 'Write a message or choose a rating.'; msg.focus(); return; }
    sendBtn.disabled = true; sendBtn.textContent = 'Sending…'; err.textContent = '';
    try {
      const r = await fetch('/api/feedback', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind: form.fbKind.value, rating, message, page: location.pathname,
                               interview_id: interviewId }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        const detail = typeof j.detail === 'string' ? j.detail
          : r.status === 401 ? 'Please log in again to send feedback.' : 'Could not send your feedback. Please try again.';
        throw new Error(detail);
      }
      body.hidden = true; done.hidden = false;
      document.dispatchEvent(new CustomEvent('feedback:sent', { detail: { rating, interviewId } }));
      closeTimer = setTimeout(() => dlg.open && dlg.close(), 2200);
    } catch (ex) {
      err.textContent = ex.message;
      sendBtn.disabled = false; sendBtn.textContent = 'Send feedback';
    }
  });
})();
