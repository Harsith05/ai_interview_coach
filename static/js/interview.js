// interview.js
// Handles TTS, timer, and question loop

(async () => {
  // Elements
  const startBtn = document.getElementById('startBtn');
  const stopBtn = document.getElementById('stopBtn');
  const questionEl = document.getElementById('question');
  const timerEl = document.getElementById('timer');

  // Config
  const timePerQuestion = 20; // seconds — change as needed
  const maxQuestions = 6;     // 5-8 recommended. We'll use what backend returns.

  let questions = [];
  let currentIndex = 0;
  let countdownInterval = null;
  let speaking = false;
  let running = false;

  // Get questions from backend
  async function loadQuestions(role) {
    try {
      const res = await fetch(`/get_questions/${role}`);
      if (!res.ok) {
        console.error('Failed to fetch questions');
        return [];
      }
      const data = await res.json();
      return data.slice(0, maxQuestions);
    } catch (err) {
      console.error(err);
      return [];
    }
  }

  // Speak text using Web Speech API
  function speak(text, onend = null) {
    if (!("speechSynthesis" in window)) {
      console.warn("Speech Synthesis not supported by this browser.");
      if (onend) onend();
      return;
    }
    const utt = new SpeechSynthesisUtterance(text);
    utt.lang = 'en-US';
    utt.pitch = 1;
    utt.rate = 1;
    utt.volume = 1;
    speaking = true;
    utt.onend = () => {
      speaking = false;
      if (typeof onend === 'function') onend();
    };
    utt.onerror = (e) => {
      speaking = false;
      console.error('SpeechSynthesis error', e);
      if (typeof onend === 'function') onend();
    };
    // speak
    speechSynthesis.cancel(); // cancel any queued speech
    speechSynthesis.speak(utt);
  }

  // Start timer for current question
  function startTimer(duration, onTimeUp) {
    let timeLeft = duration;
    timerEl.textContent = timeLeft;
    clearInterval(countdownInterval);
    countdownInterval = setInterval(() => {
      timeLeft--;
      timerEl.textContent = timeLeft;
      if (timeLeft <= 0) {
        clearInterval(countdownInterval);
        timerEl.textContent = "0";
        if (typeof onTimeUp === 'function') onTimeUp();
      }
    }, 1000);
  }

  // Ask next question (speaks it and starts timer)
  function askCurrentQuestion() {
    if (!running) return;
    if (currentIndex >= questions.length) {
      questionEl.innerText = "Interview complete. Thank you!";
      timerEl.innerText = "--";
      stopInterview();
      // Here you could redirect to feedback page or call backend to compute results
      return;
    }

    const q = questions[currentIndex];
    questionEl.innerText = q;

    // Speak question then start timer immediately (or start timer in parallel)
    // If you want the candidate to start answering AFTER the speech finishes,
    // start the timer in the onend callback. We start the timer after speaking ends.
    speak(q, () => {
      // Start countdown AFTER speech ends (so user hears whole question)
      startTimer(timePerQuestion, () => {
        // Time up for this question
        currentIndex++;
        // small delay before next question to give UX breathing room
        setTimeout(askCurrentQuestion, 700);
      });
    });
  }

  async function startInterview() {
    if (running) return;
    startBtn.disabled = true;
    stopBtn.disabled = false;
    questionEl.innerText = "Preparing interview...";
    timerEl.innerText = "--";
    running = true;

    // load questions for the role
    const role = window.INTERVIEW_ROLE || 'ml';
    questions = await loadQuestions(role);
    if (!questions || questions.length === 0) {
      questionEl.innerText = "No questions found for the selected domain.";
      startBtn.disabled = false;
      stopBtn.disabled = true;
      running = false;
      return;
    }
    currentIndex = 0;
    // small delay to ensure browser allows TTS after user interaction
    setTimeout(askCurrentQuestion, 300);
  }

  function stopInterview() {
    running = false;
    startBtn.disabled = false;
    stopBtn.disabled = true;
    clearInterval(countdownInterval);
    speechSynthesis.cancel();
    questionEl.innerText = "Interview stopped.";
    timerEl.innerText = "--";
  }

  // Attach events
  startBtn.addEventListener('click', startInterview);
  stopBtn.addEventListener('click', stopInterview);

})();
