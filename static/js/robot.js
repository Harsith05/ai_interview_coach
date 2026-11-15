// Animated robot avatar shown in the bottom-right corner of the voice pages.
//
// <script src="/static/js/robot.js" data-robot="atlas|nova|pip|quill" data-name="Atlas"></script>
//
// It lip-syncs to the AI voice automatically (it watches speechSynthesis.speak), shows what it
// says in a speech bubble word by word, blinks, follows the mouse with its eyes, and reacts to
// state changes from the page:  Robot.state('idle'|'listening'|'thinking'|'sleep', note?),
// Robot.level(0-100) (mic level → ear lights), Robot.say(text) (bubble without speaking).
(() => {
  const script = document.currentScript;
  const KIND = script?.dataset.robot || 'pip';
  const NAME = script?.dataset.name || '';

  // ---------------------------------------------------------------- the four robots (original art)
  // Shared class names drive the animations: rb-body, rb-head, rb-eye, rb-pupil, rb-mouth,
  // rb-antenna, rb-ear, rb-arm-l, rb-arm-r, rb-led, rb-shadow.
  const ROBOTS = {
    // Interview: a formal steel interviewer with a tie.
    atlas: `
<svg viewBox="0 0 200 250" class="rb-svg" aria-hidden="true">
 <defs>
  <linearGradient id="at-metal" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f1f5f9"/><stop offset=".55" stop-color="#a7b6c4"/><stop offset="1" stop-color="#5f7486"/></linearGradient>
  <linearGradient id="at-blue" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#63b3e0"/><stop offset="1" stop-color="#1f5f8b"/></linearGradient>
  <radialGradient id="at-eye" cx=".45" cy=".4" r=".65"><stop offset="0" stop-color="#d4f8ff"/><stop offset=".55" stop-color="#2bc4ec"/><stop offset="1" stop-color="#0b6f93"/></radialGradient>
  <linearGradient id="at-screen" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1b3a50"/><stop offset="1" stop-color="#0a1a26"/></linearGradient>
 </defs>
 <ellipse class="rb-shadow" cx="100" cy="242" rx="52" ry="7"/>
 <g class="rb-body">
  <rect x="76" y="196" width="18" height="30" rx="7" fill="url(#at-metal)"/>
  <rect x="106" y="196" width="18" height="30" rx="7" fill="url(#at-metal)"/>
  <rect x="66" y="222" width="32" height="13" rx="6.5" fill="url(#at-blue)"/>
  <rect x="102" y="222" width="32" height="13" rx="6.5" fill="url(#at-blue)"/>
  <g class="rb-arm-l"><rect x="40" y="128" width="18" height="56" rx="9" fill="url(#at-metal)" stroke="#6b8193" stroke-width="1.5"/><circle cx="49" cy="188" r="10" fill="url(#at-blue)"/></g>
  <rect x="58" y="118" width="84" height="84" rx="18" fill="url(#at-metal)" stroke="#5f7486" stroke-width="2"/>
  <path d="M84 119 L100 137 L116 119 Z" fill="#fff"/>
  <path d="M95 127 L105 127 L104 136 L96 136 Z" fill="#a93226"/>
  <path d="M96 135 L104 135 L108 167 L100 178 L92 167 Z" fill="#c0392b"/>
  <path d="M97 145 L106 152 M95 157 L107 164" stroke="#e66b5b" stroke-width="2"/>
  <rect x="66" y="148" width="20" height="30" rx="4" fill="url(#at-screen)"/>
  <circle class="rb-led" cx="76" cy="157" r="3.2" fill="#2bc4ec"/><circle cx="76" cy="168" r="3.2" fill="#f5b041"/>
  <circle cx="124" cy="158" r="8" fill="url(#at-blue)" stroke="#e3ebf2" stroke-width="2.5"/>
  <circle cx="124" cy="182" r="3" fill="#6b8193"/>
  <g class="rb-arm-r"><rect x="142" y="128" width="18" height="56" rx="9" fill="url(#at-metal)" stroke="#6b8193" stroke-width="1.5"/><circle cx="151" cy="188" r="10" fill="url(#at-blue)"/></g>
  <rect x="88" y="106" width="24" height="16" rx="5" fill="#6b8193"/>
  <g class="rb-head">
   <line x1="100" y1="28" x2="100" y2="12" stroke="#6b8193" stroke-width="4" stroke-linecap="round"/>
   <circle class="rb-antenna" cx="100" cy="10" r="6.5" fill="#e74c3c"/>
   <circle cx="42" cy="68" r="12" fill="url(#at-blue)"/><circle class="rb-ear" cx="42" cy="68" r="5"/>
   <circle cx="158" cy="68" r="12" fill="url(#at-blue)"/><circle class="rb-ear" cx="158" cy="68" r="5"/>
   <rect x="46" y="26" width="108" height="86" rx="24" fill="url(#at-metal)" stroke="#5f7486" stroke-width="2"/>
   <rect x="58" y="38" width="84" height="60" rx="16" fill="url(#at-screen)"/>
   <g class="rb-eye"><circle cx="82" cy="62" r="13" fill="url(#at-eye)"/><g class="rb-pupil"><circle cx="82" cy="62" r="5.5" fill="#06283a"/><circle cx="84.5" cy="59.5" r="2" fill="#fff"/></g></g>
   <g class="rb-eye"><circle cx="118" cy="62" r="13" fill="url(#at-eye)"/><g class="rb-pupil"><circle cx="118" cy="62" r="5.5" fill="#06283a"/><circle cx="120.5" cy="59.5" r="2" fill="#fff"/></g></g>
   <rect class="rb-mouth" x="87" y="82" width="26" height="7" rx="3.5" fill="#2bc4ec"/>
   <ellipse cx="78" cy="33" rx="22" ry="4.5" fill="#fff" opacity=".55"/>
  </g>
 </g>
</svg>`,

    // Talk: a floating white companion with a glowing visor.
    nova: `
<svg viewBox="0 0 200 250" class="rb-svg" aria-hidden="true">
 <defs>
  <radialGradient id="nv-shell" cx=".38" cy=".3" r=".85"><stop offset="0" stop-color="#ffffff"/><stop offset=".6" stop-color="#e7edf4"/><stop offset="1" stop-color="#b7c3d1"/></radialGradient>
  <linearGradient id="nv-visor" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1d2a44"/><stop offset="1" stop-color="#05080f"/></linearGradient>
  <radialGradient id="nv-glow" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#e6fdff"/><stop offset=".45" stop-color="#3fe0ff"/><stop offset="1" stop-color="#0aa5d6" stop-opacity=".9"/></radialGradient>
 </defs>
 <ellipse class="rb-shadow" cx="100" cy="242" rx="40" ry="6"/>
 <g class="rb-body">
  <ellipse class="rb-led" cx="100" cy="221" rx="16" ry="5" fill="#3fe0ff" opacity=".55"/>
  <g class="rb-arm-l"><ellipse cx="56" cy="168" rx="11" ry="24" transform="rotate(22 56 168)" fill="url(#nv-shell)" stroke="#c3ccd8" stroke-width="1.5"/></g>
  <g class="rb-arm-r"><ellipse cx="144" cy="168" rx="11" ry="24" transform="rotate(-22 144 168)" fill="url(#nv-shell)" stroke="#c3ccd8" stroke-width="1.5"/></g>
  <ellipse cx="100" cy="174" rx="42" ry="44" fill="url(#nv-shell)" stroke="#c3ccd8" stroke-width="1.5"/>
  <circle class="rb-led" cx="100" cy="174" r="10" fill="url(#nv-glow)"/>
  <path d="M74 150 Q100 142 126 150" stroke="#cdd6e1" stroke-width="2" fill="none"/>
  <g class="rb-head">
   <rect x="30" y="62" width="16" height="38" rx="8" fill="url(#nv-shell)" stroke="#c3ccd8" stroke-width="1.5"/>
   <rect class="rb-ear" x="34" y="72" width="8" height="18" rx="4"/>
   <rect x="154" y="62" width="16" height="38" rx="8" fill="url(#nv-shell)" stroke="#c3ccd8" stroke-width="1.5"/>
   <rect class="rb-ear" x="158" y="72" width="8" height="18" rx="4"/>
   <ellipse cx="100" cy="82" rx="62" ry="54" fill="url(#nv-shell)" stroke="#c3ccd8" stroke-width="1.5"/>
   <rect x="52" y="50" width="96" height="64" rx="32" fill="url(#nv-visor)"/>
   <g class="rb-eye"><rect x="70" y="64" width="20" height="28" rx="10" fill="url(#nv-glow)"/><g class="rb-pupil"><ellipse cx="80" cy="72" rx="4" ry="5" fill="#ffffff" opacity=".9"/></g></g>
   <g class="rb-eye"><rect x="110" y="64" width="20" height="28" rx="10" fill="url(#nv-glow)"/><g class="rb-pupil"><ellipse cx="120" cy="72" rx="4" ry="5" fill="#ffffff" opacity=".9"/></g></g>
   <path class="rb-mouth" d="M88 100 Q100 109 112 100" stroke="#3fe0ff" stroke-width="4" stroke-linecap="round" fill="none"/>
   <ellipse cx="78" cy="40" rx="24" ry="6" fill="#fff" opacity=".9"/>
   <circle class="rb-antenna" cx="100" cy="30" r="4" fill="#3fe0ff"/>
  </g>
 </g>
</svg>`,

    // Speaking practice: a cheerful blue robot with orange eyes.
    pip: `
<svg viewBox="0 0 200 250" class="rb-svg" aria-hidden="true">
 <defs>
  <linearGradient id="pp-blue" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#7cc8ff"/><stop offset=".6" stop-color="#2f80ed"/><stop offset="1" stop-color="#1b4fa8"/></linearGradient>
  <radialGradient id="pp-orange" cx=".4" cy=".35" r=".7"><stop offset="0" stop-color="#ffe29a"/><stop offset=".55" stop-color="#ffa62b"/><stop offset="1" stop-color="#d9700c"/></radialGradient>
  <linearGradient id="pp-face" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#f4fbff"/><stop offset="1" stop-color="#cfe6f8"/></linearGradient>
  <linearGradient id="pp-white" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#d5dde6"/></linearGradient>
 </defs>
 <ellipse class="rb-shadow" cx="100" cy="242" rx="46" ry="7"/>
 <g class="rb-body">
  <rect x="78" y="198" width="16" height="26" rx="7" fill="url(#pp-blue)"/>
  <rect x="106" y="198" width="16" height="26" rx="7" fill="url(#pp-blue)"/>
  <ellipse cx="84" cy="230" rx="17" ry="8" fill="url(#pp-white)"/>
  <ellipse cx="116" cy="230" rx="17" ry="8" fill="url(#pp-white)"/>
  <g class="rb-arm-l"><rect x="44" y="134" width="16" height="46" rx="8" fill="url(#pp-blue)"/><circle cx="52" cy="184" r="11" fill="url(#pp-white)"/></g>
  <rect x="60" y="124" width="80" height="80" rx="24" fill="url(#pp-blue)" stroke="#1b4fa8" stroke-width="1.5"/>
  <rect x="74" y="138" width="52" height="46" rx="16" fill="url(#pp-white)"/>
  <circle class="rb-led" cx="88" cy="160" r="5" fill="url(#pp-orange)"/><circle cx="100" cy="160" r="5" fill="#2f80ed"/><circle class="rb-led" cx="112" cy="160" r="5" fill="url(#pp-orange)"/>
  <rect x="86" y="171" width="28" height="4" rx="2" fill="#9cc4ea"/>
  <g class="rb-arm-r"><rect x="140" y="134" width="16" height="46" rx="8" fill="url(#pp-blue)"/><circle cx="148" cy="184" r="11" fill="url(#pp-white)"/></g>
  <rect x="90" y="114" width="20" height="14" rx="5" fill="#1b4fa8"/>
  <g class="rb-head">
   <line x1="100" y1="24" x2="100" y2="8" stroke="#1b4fa8" stroke-width="4" stroke-linecap="round"/>
   <circle class="rb-antenna" cx="100" cy="7" r="7" fill="url(#pp-orange)"/>
   <circle cx="46" cy="70" r="10" fill="url(#pp-orange)"/><circle class="rb-ear" cx="46" cy="70" r="4"/>
   <circle cx="154" cy="70" r="10" fill="url(#pp-orange)"/><circle class="rb-ear" cx="154" cy="70" r="4"/>
   <circle cx="100" cy="70" r="52" fill="url(#pp-blue)" stroke="#1b4fa8" stroke-width="1.5"/>
   <rect x="64" y="44" width="72" height="58" rx="22" fill="url(#pp-face)"/>
   <g class="rb-eye"><circle cx="84" cy="68" r="12" fill="url(#pp-orange)"/><g class="rb-pupil"><circle cx="84" cy="68" r="6" fill="#2b1a05"/><circle cx="86.5" cy="65.5" r="2.2" fill="#fff"/></g></g>
   <g class="rb-eye"><circle cx="116" cy="68" r="12" fill="url(#pp-orange)"/><g class="rb-pupil"><circle cx="116" cy="68" r="6" fill="#2b1a05"/><circle cx="118.5" cy="65.5" r="2.2" fill="#fff"/></g></g>
   <ellipse cx="74" cy="86" rx="6" ry="3.5" fill="#ff9aa2" opacity=".6"/><ellipse cx="126" cy="86" rx="6" ry="3.5" fill="#ff9aa2" opacity=".6"/>
   <path class="rb-mouth" d="M90 88 Q100 97 110 88 Z" fill="#2b1a05"/>
   <ellipse cx="84" cy="28" rx="22" ry="6" fill="#fff" opacity=".4"/>
  </g>
 </g>
</svg>`,

    // My questions: a retro beige robot with red headphones and a "?" screen.
    quill: `
<svg viewBox="0 0 200 250" class="rb-svg" aria-hidden="true">
 <defs>
  <linearGradient id="ql-beige" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fbf0d9"/><stop offset=".6" stop-color="#e2cda2"/><stop offset="1" stop-color="#b89a68"/></linearGradient>
  <linearGradient id="ql-red" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ff7a66"/><stop offset="1" stop-color="#c0392b"/></linearGradient>
  <radialGradient id="ql-eye" cx=".4" cy=".35" r=".7"><stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#e8e2d6"/></radialGradient>
 </defs>
 <ellipse class="rb-shadow" cx="100" cy="242" rx="48" ry="7"/>
 <g class="rb-body">
  <rect x="78" y="196" width="14" height="28" rx="5" fill="url(#ql-beige)" stroke="#9c825a" stroke-width="1.5"/>
  <rect x="108" y="196" width="14" height="28" rx="5" fill="url(#ql-beige)" stroke="#9c825a" stroke-width="1.5"/>
  <circle cx="85" cy="208" r="5" fill="#4a4036"/><circle cx="115" cy="208" r="5" fill="#4a4036"/>
  <rect x="68" y="222" width="30" height="12" rx="5" fill="url(#ql-red)"/>
  <rect x="102" y="222" width="30" height="12" rx="5" fill="url(#ql-red)"/>
  <g class="rb-arm-l"><rect x="44" y="130" width="14" height="50" rx="6" fill="url(#ql-beige)" stroke="#9c825a" stroke-width="1.5"/><circle cx="51" cy="156" r="5" fill="#4a4036"/><path d="M42 182 L51 196 L60 182" stroke="#4a4036" stroke-width="5" fill="none" stroke-linecap="round"/></g>
  <rect x="58" y="120" width="84" height="82" rx="12" fill="url(#ql-beige)" stroke="#9c825a" stroke-width="2"/>
  <circle cx="66" cy="128" r="2.5" fill="#9c825a"/><circle cx="134" cy="128" r="2.5" fill="#9c825a"/><circle cx="66" cy="194" r="2.5" fill="#9c825a"/><circle cx="134" cy="194" r="2.5" fill="#9c825a"/>
  <rect x="74" y="134" width="36" height="30" rx="6" fill="#203040"/>
  <text class="rb-led" x="92" y="157" text-anchor="middle" font-family="Arial, sans-serif" font-weight="700" font-size="22" fill="#7ef9b0">?</text>
  <circle cx="124" cy="148" r="9" fill="#f5a623" stroke="#9c825a" stroke-width="2"/><path d="M124 148 L129 143" stroke="#4a4036" stroke-width="2"/>
  <rect x="74" y="172" width="52" height="5" rx="2.5" fill="#c9b285"/><rect x="74" y="182" width="36" height="5" rx="2.5" fill="#c9b285"/>
  <g class="rb-arm-r"><rect x="142" y="130" width="14" height="50" rx="6" fill="url(#ql-beige)" stroke="#9c825a" stroke-width="1.5"/><circle cx="149" cy="156" r="5" fill="#4a4036"/><path d="M140 182 L149 196 L158 182" stroke="#4a4036" stroke-width="5" fill="none" stroke-linecap="round"/></g>
  <rect x="90" y="108" width="20" height="14" rx="3" fill="#9c825a"/>
  <g class="rb-head">
   <path d="M50 66 Q50 18 100 18 Q150 18 150 66" stroke="url(#ql-red)" stroke-width="8" fill="none" stroke-linecap="round"/>
   <rect x="52" y="32" width="96" height="78" rx="16" fill="url(#ql-beige)" stroke="#9c825a" stroke-width="2"/>
   <rect x="38" y="54" width="18" height="34" rx="8" fill="url(#ql-red)"/><rect class="rb-ear" x="42" y="64" width="6" height="14" rx="3"/>
   <rect x="144" y="54" width="18" height="34" rx="8" fill="url(#ql-red)"/><rect class="rb-ear" x="152" y="64" width="6" height="14" rx="3"/>
   <path class="rb-brow" d="M70 47 Q81 40 92 45 M108 45 Q119 40 130 47" stroke="#4a4036" stroke-width="3.5" stroke-linecap="round" fill="none"/>
   <g class="rb-eye"><circle cx="82" cy="64" r="13" fill="url(#ql-eye)" stroke="#4a4036" stroke-width="3"/><g class="rb-pupil"><circle cx="82" cy="64" r="6" fill="#1d1a17"/><circle cx="84.5" cy="61.5" r="2" fill="#fff"/></g></g>
   <g class="rb-eye"><circle cx="118" cy="64" r="13" fill="url(#ql-eye)" stroke="#4a4036" stroke-width="3"/><g class="rb-pupil"><circle cx="118" cy="64" r="6" fill="#1d1a17"/><circle cx="120.5" cy="61.5" r="2" fill="#fff"/></g></g>
   <g class="rb-mouth"><path d="M78 85 H122 Q119 103 100 103 Q81 103 78 85 Z" fill="#3a2f26"/>
    <path d="M80 85 H120 L119 90 H81 Z" fill="#f7efe0"/>
    <path d="M90 85 V90 M100 85 V90 M110 85 V90" stroke="#c9bfae" stroke-width="1.2"/>
    <path d="M90 99 Q100 95 110 99" stroke="#c0392b" stroke-width="4" stroke-linecap="round" fill="none"/></g>
   <ellipse cx="76" cy="38" rx="18" ry="4" fill="#fff" opacity=".5"/>
  </g>
 </g>
</svg>`,
  };

  // ---------------------------------------------------------------- styles
  const css = `
  .rb-wrap { position: fixed; right: 14px; bottom: 6px; z-index: 60; display: flex; align-items: flex-end;
             gap: 4px; pointer-events: none; font-family: inherit; }
  .rb-bot { width: 168px; height: 210px; flex: none; position: relative; }
  .rb-svg { width: 100%; height: 100%; overflow: visible; filter: drop-shadow(0 6px 10px rgba(0,0,0,.12)); }
  .rb-bubble { max-width: 320px; background: #fff; color: #1f2933; border-radius: 16px; padding: 10px 14px 9px;
               box-shadow: 0 6px 22px rgba(0,0,0,.14); font-size: 14.5px; line-height: 1.42; margin-bottom: 126px;
               position: relative; opacity: 0; transform: translateY(8px) scale(.97); transition: opacity .25s, transform .25s; }
  .rb-bubble.show { opacity: 1; transform: none; }
  .rb-bubble::after { content: ""; position: absolute; right: -9px; bottom: 18px; border: 9px solid transparent;
                      border-left-color: #fff; border-right: 0; }
  .rb-bubble .rb-name { font-size: 11.5px; font-weight: 700; color: #6b7280; letter-spacing: .03em; text-transform: uppercase; }
  .rb-bubble .rb-text { max-height: 9.5em; overflow: auto; }
  .rb-bubble .rb-text .later { color: #9aa3ad; }
  .rb-bubble .rb-note { font-size: 12px; color: #6b7280; margin-top: 4px; }
  .rb-bubble .rb-note:empty { display: none; }
  .rb-toggle { position: absolute; top: 2px; right: 2px; pointer-events: auto; width: 22px; height: 22px; border-radius: 50%;
               border: 1px solid #e5e7eb; background: #fff; color: #6b7280; font-size: 13px; line-height: 19px; cursor: pointer;
               box-shadow: 0 1px 4px rgba(0,0,0,.1); padding: 0; }
  .rb-wrap.rb-mini .rb-bot { width: 84px; height: 105px; }
  .rb-wrap.rb-mini .rb-bubble { display: none; }
  @media (max-width: 700px) {
    .rb-bot { width: 104px; height: 130px; }
    .rb-bubble { max-width: 210px; font-size: 13px; margin-bottom: 80px; }
  }

  .rb-shadow { fill: rgba(0,0,0,.16); transform-box: fill-box; transform-origin: center; animation: rb-shadow 3.4s ease-in-out infinite; }
  .rb-body { animation: rb-bob 3.4s ease-in-out infinite; }
  @keyframes rb-bob { 50% { transform: translateY(-5px); } }
  @keyframes rb-shadow { 50% { transform: scaleX(.86); opacity: .7; } }
  .rb-head { transform-box: fill-box; transform-origin: 50% 92%; transition: transform .45s cubic-bezier(.3,1.4,.5,1); }
  .rb-listening .rb-head { transform: rotate(-6deg); }
  .rb-thinking .rb-head { transform: rotate(5deg) translateY(-2px); }
  .rb-sleep .rb-head { transform: rotate(9deg) translateY(4px); }
  .rb-speaking .rb-head { animation: rb-nod 2.6s ease-in-out infinite; }
  @keyframes rb-nod { 25% { transform: rotate(-2deg); } 75% { transform: rotate(2deg); } }
  .rb-eye { transform-box: fill-box; transform-origin: center; transition: transform .07s; }
  .rb-blink .rb-eye { transform: scaleY(.08); }
  .rb-sleep .rb-eye { transform: scaleY(.14); }
  .rb-listening .rb-eye { transform: scale(1.08); }
  .rb-pupil { transition: transform .22s ease-out; }
  .rb-mouth { transform-box: fill-box; transform-origin: center; transform: scaleY(var(--rb-mouth, 1)); }
  .rb-brow { transition: transform .3s; transform-box: fill-box; transform-origin: center; }
  .rb-thinking .rb-brow { transform: translateY(-3px); }
  .rb-antenna { transform-box: fill-box; transform-origin: center; }
  .rb-thinking .rb-antenna { animation: rb-flash .45s steps(2) infinite; }
  .rb-speaking .rb-antenna { animation: rb-pulse .9s ease-in-out infinite; }
  .rb-ear { fill: #7ef9ff; opacity: calc(.3 + var(--rb-level, 0) * .7); transition: opacity .08s; }
  .rb-listening .rb-ear { filter: drop-shadow(0 0 3px #4ff); }
  .rb-arm-r { transform-box: fill-box; transform-origin: 50% 6%; }
  .rb-arm-l { transform-box: fill-box; transform-origin: 50% 6%; animation: rb-sway 3.4s ease-in-out infinite; }
  @keyframes rb-sway { 50% { transform: rotate(4deg); } }
  .rb-wave .rb-arm-r { animation: rb-wave 1.6s ease-in-out; }
  @keyframes rb-wave { 15% { transform: rotate(-150deg); } 30% { transform: rotate(-118deg); } 45% { transform: rotate(-150deg); }
                       60% { transform: rotate(-118deg); } 75% { transform: rotate(-145deg); } 100% { transform: rotate(0); } }
  .rb-speaking .rb-arm-r { animation: rb-gesture 3s ease-in-out infinite; }
  @keyframes rb-gesture { 30% { transform: rotate(-22deg); } 60% { transform: rotate(-8deg); } }
  .rb-led { animation: rb-pulse 2.2s ease-in-out infinite; }
  @keyframes rb-pulse { 50% { opacity: .45; } }
  @keyframes rb-flash { 50% { opacity: .15; } }
  .rb-zzz { position: absolute; right: 22px; top: 6px; font-weight: 700; color: #7b8794; font-size: 15px; display: none;
            animation: rb-zzz 2.4s ease-in-out infinite; }
  .rb-sleep .rb-zzz { display: block; }
  @keyframes rb-zzz { 0% { opacity: 0; transform: translate(0, 6px); } 50% { opacity: 1; } 100% { opacity: 0; transform: translate(8px, -10px); } }
  @media (prefers-reduced-motion: reduce) {
    .rb-body, .rb-shadow, .rb-led, .rb-arm-l, .rb-speaking .rb-head, .rb-speaking .rb-arm-r, .rb-zzz { animation: none; }
  }`;

  // ---------------------------------------------------------------- mount
  const LS_MINI = 'aic.robotMini';
  let wrap, bubble, textEl, noteEl, svgEl;
  let state = 'idle', speaking = false;
  let spokenText = '', spokenUpTo = 0;
  const pupilTarget = { x: 0, y: 0 };

  function mount() {
    if (!ROBOTS[KIND] || document.querySelector('.rb-wrap')) return;
    const style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);

    wrap = document.createElement('div');
    wrap.className = 'rb-wrap rb-idle';
    wrap.innerHTML = `
      <div class="rb-bubble" role="status" aria-live="polite">
        <div class="rb-name"></div>
        <div class="rb-text"></div><div class="rb-note"></div>
      </div>
      <div class="rb-bot">${ROBOTS[KIND]}<div class="rb-zzz">z z</div>
        <button class="rb-toggle" type="button" title="Minimize / restore the robot">–</button></div>`;
    document.body.appendChild(wrap);
    bubble = wrap.querySelector('.rb-bubble');
    textEl = wrap.querySelector('.rb-text');
    noteEl = wrap.querySelector('.rb-note');
    svgEl = wrap.querySelector('svg');
    const nameEl = wrap.querySelector('.rb-name');
    if (NAME) nameEl.textContent = NAME; else nameEl.remove();
    // Room to scroll content out from under the robot.
    document.body.style.paddingBottom = '200px';

    const toggle = wrap.querySelector('.rb-toggle');
    const setMini = (on) => {
      wrap.classList.toggle('rb-mini', on);
      toggle.textContent = on ? '+' : '–';
      try { localStorage.setItem(LS_MINI, on ? '1' : '0'); } catch {}
    };
    toggle.addEventListener('click', () => setMini(!wrap.classList.contains('rb-mini')));
    try { if (localStorage.getItem(LS_MINI) === '1') setMini(true); } catch {}

    scheduleBlink();
    document.addEventListener('mousemove', onMouse, { passive: true });
    setTimeout(wave, 600);
  }

  // ---------------------------------------------------------------- life-like behaviour
  function scheduleBlink() {
    setTimeout(() => {
      if (state !== 'sleep') {
        wrap.classList.add('rb-blink');
        setTimeout(() => wrap.classList.remove('rb-blink'), 130);
        if (Math.random() < 0.2) setTimeout(() => {          // occasional double blink
          wrap.classList.add('rb-blink');
          setTimeout(() => wrap.classList.remove('rb-blink'), 120);
        }, 260);
      }
      scheduleBlink();
    }, 2200 + Math.random() * 4200);
  }

  function setPupils(x, y) {
    wrap?.querySelectorAll('.rb-pupil').forEach(p => { p.style.transform = `translate(${x}px, ${y}px)`; });
  }

  function onMouse(e) {
    if (!svgEl || state === 'thinking' || state === 'sleep') return;
    const r = svgEl.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height * 0.28;
    const dx = e.clientX - cx, dy = e.clientY - cy;
    const d = Math.hypot(dx, dy) || 1;
    const k = Math.min(1, d / 300) * 4;
    pupilTarget.x = (dx / d) * k;
    pupilTarget.y = (dy / d) * k * 0.8;
    setPupils(pupilTarget.x, pupilTarget.y);
  }

  function wave() {
    if (!wrap) return;
    wrap.classList.remove('rb-wave');
    void wrap.offsetWidth;
    wrap.classList.add('rb-wave');
    setTimeout(() => wrap.classList.remove('rb-wave'), 1700);
  }

  // ---------------------------------------------------------------- lip sync (visemes)
  // Browsers don't expose the synthesized audio, so lip sync is driven by the text: every word
  // is turned into mouth shapes (visemes) and played over that word's duration, starting at
  // the moment the voice reports it begins the word (speech "boundary" events). Voices that
  // don't report word boundaries fall back to a timeline estimated from the whole sentence.
  //   [open 0..1, width ~0.6 (rounded) .. 1.3 (spread)]
  const VISEME = {
    a: [.95, 1.0], h: [.55, 1.0], e: [.55, 1.22], i: [.45, 1.26], y: [.45, 1.22],
    o: [.78, .7], u: [.42, .6], w: [.32, .58], q: [.36, .6],
    m: [0, 1.0], b: [0, 1.0], p: [0, 1.0], f: [.14, 1.05], v: [.14, 1.05],
    l: [.34, 1.05], n: [.28, 1.05], t: [.3, 1.08], d: [.32, 1.05], s: [.2, 1.15], z: [.2, 1.12],
    c: [.3, 1.08], k: [.36, 1.0], g: [.36, 1.0], j: [.3, .9], r: [.34, .84], x: [.3, 1.05],
  };
  const VOWELS = 'aeiouy';
  // Vowel pairs that make one sound → one mouth shape.
  const DIGRAPH = { ea: 'e', ee: 'e', ie: 'e', ei: 'e', ey: 'e', oo: 'u', ou: 'o', ue: 'u', ew: 'u', ui: 'u',
                    oa: 'o', ow: 'o', oe: 'o', ai: 'a', ay: 'a', au: 'o', aw: 'o', oy: 'o', oi: 'o' };

  function wordVisemes(word) {
    let w = word.toLowerCase().replace(/[^a-z]/g, '');
    if (w.length > 3 && /[aeiou][^aeiouy]e$/.test(w)) w = w.slice(0, -1);   // silent final e: make, time, please
    const out = [];
    for (let i = 0; i < w.length; i++) {
      let ch = w[i];
      if ('tscw'.includes(ch) && w[i + 1] === 'h') { out.push({ o: .22, w: ch === 'w' ? .62 : 1.08, d: 1 }); i++; continue; }
      if (ch === w[i - 1]) continue;                              // doubled letters: one shape
      const pair = DIGRAPH[ch + (w[i + 1] || '')];
      if (pair) { ch = pair; i++; }
      const v = VISEME[ch];
      if (!v) continue;
      out.push({ o: v[0], w: v[1], d: VOWELS.includes(ch) ? (pair ? 2 : 1.6) : 0.75 });
    }
    if (/^\d/.test(word)) out.push({ o: .5, w: 1.1, d: 2 }, { o: .3, w: 1, d: 1 });     // numbers
    return out.length ? out : [{ o: .3, w: 1, d: 1 }];
  }

  function syllables(word) {
    const groups = word.toLowerCase().replace(/[^a-z]/g, '').replace(/e$/, '').match(/[aeiouy]+/g);
    return Math.max(1, groups ? groups.length : 1);
  }

  function wordDuration(word, rate) {      // ms, at the utterance's speaking rate
    return Math.min(1100, Math.max(130, (syllables(word) * 205 + 30) / (rate || 1)));
  }

  const lip = { timeline: [], cur: { o: 0, w: 1 }, raf: null, gotBoundary: false, rate: 1, fallbackTimer: null };

  function schedule(word, startAt) {
    const vs = wordVisemes(word);
    const dur = wordDuration(word, lip.rate);
    const total = vs.reduce((a, v) => a + v.d, 0);
    let t = startAt;
    for (const v of vs) { lip.timeline.push({ t, o: v.o, w: v.w }); t += (v.d / total) * dur * 0.92; }
    lip.timeline.push({ t, o: 0.04, w: 1 });                    // lips meet between words
    return startAt + dur;
  }

  function lipWord(word) {                  // a word is starting now
    const now = performance.now();
    lip.timeline = lip.timeline.filter(e => e.t < now);         // drop leftovers of the previous word
    schedule(word, now);
    ensureLipLoop();
  }

  function lipFallback(text) {              // no boundary events: estimate the whole sentence
    let t = performance.now();
    for (const token of text.split(/\s+/).filter(Boolean)) {
      t = schedule(token, t) + 45;
      if (/[,;:]$/.test(token)) t += 190;
      if (/[.!?]$/.test(token)) t += 360;
    }
    ensureLipLoop();
  }

  function ensureLipLoop() {
    if (lip.raf) return;
    const step = () => {
      const now = performance.now();
      let target = { o: 0, w: 1 };
      while (lip.timeline.length > 1 && lip.timeline[1].t <= now) lip.timeline.shift();
      if (speaking && lip.timeline.length && lip.timeline[0].t <= now) target = lip.timeline[0];
      // Ease toward the target: quick to open, slightly slower to close, like real lips.
      const k = target.o > lip.cur.o ? 0.55 : 0.4;
      lip.cur.o += (target.o - lip.cur.o) * k;
      lip.cur.w += (target.w - lip.cur.w) * 0.4;
      applyMouth();
      if (!speaking && lip.cur.o < 0.01) { lip.raf = null; lip.cur = { o: 0, w: 1 }; applyMouth(); return; }
      lip.raf = requestAnimationFrame(step);
    };
    lip.raf = requestAnimationFrame(step);
  }

  function applyMouth() {
    if (!wrap) return;
    const { o, w } = lip.cur;
    wrap.style.setProperty('--rb-open', o.toFixed(3));
    wrap.style.setProperty('--rb-wide', w.toFixed(3));
    // Cartoon robots: one "mouth" scaled vertically (rest shape = 1).
    const rest = state === 'listening' ? 0.55 : 1;
    wrap.style.setProperty('--rb-mouth', (speaking || o > 0.01 ? 0.3 + o * 2.1 : rest).toFixed(3));
  }

  function lipStart(u) {
    lip.timeline = [];
    lip.gotBoundary = false;
    lip.rate = u.rate || 1;
    clearTimeout(lip.fallbackTimer);
    const armFallback = () => {
      lip.fallbackTimer = setTimeout(() => { if (speaking && !lip.gotBoundary) lipFallback(u.text || ''); }, 420);
    };
    u.addEventListener('start', armFallback);
    armFallback();      // in case 'start' never fires
    ensureLipLoop();
  }

  function lipStop() {
    clearTimeout(lip.fallbackTimer);
    lip.timeline = [];
    ensureLipLoop();    // eases the mouth shut, then stops
  }

  function renderText() {
    if (!textEl) return;
    const done = spokenText.slice(0, spokenUpTo), later = spokenText.slice(spokenUpTo);
    textEl.innerHTML = '';
    textEl.append(document.createTextNode(done));
    if (later) {
      const s = document.createElement('span');
      s.className = 'later';
      s.textContent = later;
      textEl.append(s);
    }
    textEl.scrollTop = textEl.scrollHeight;
  }

  // ---------------------------------------------------------------- public API
  function setState(s, note) {
    if (!wrap) return;
    state = s;
    wrap.classList.remove('rb-idle', 'rb-listening', 'rb-thinking', 'rb-sleep', 'rb-speaking');
    wrap.classList.add('rb-' + (speaking && s !== 'sleep' ? 'speaking' : s));
    if (s === 'thinking') setPupils(2.5, -3.5);
    else if (s === 'listening') setPupils(-1.5, 0.5);
    else setPupils(pupilTarget.x, pupilTarget.y);
    if (!speaking) applyMouth();
    if (s !== 'listening') wrap.style.setProperty('--rb-level', 0);
    const notes = { listening: '🎙️ Listening…', thinking: '💭 Thinking…', sleep: 'Say my name to wake me' };
    noteEl.textContent = note ?? notes[s] ?? '';
    if (s === 'sleep') bubble.classList.remove('show');
    else if (spokenText || noteEl.textContent) bubble.classList.add('show');
  }

  function say(text, note) {
    if (!wrap) return;
    spokenText = text || '';
    spokenUpTo = spokenText.length;
    renderText();
    if (note !== undefined) noteEl.textContent = note;
    bubble.classList.toggle('show', !!(spokenText || noteEl.textContent));
  }

  function level(pct) {
    wrap?.style.setProperty('--rb-level', Math.max(0, Math.min(1, pct / 60)).toFixed(2));
  }

  // ---------------------------------------------------------------- follow the AI voice
  function onUtterance(u) {
    if (!wrap) return;
    speaking = true;
    state = 'idle';           // speaking replaces "thinking"; the page sets "listening" afterwards
    spokenText = u.text || '';
    spokenUpTo = 0;
    renderText();
    noteEl.textContent = '';
    bubble.classList.add('show');
    wrap.classList.remove('rb-idle', 'rb-listening', 'rb-thinking', 'rb-sleep');
    wrap.classList.add('rb-speaking');
    lipStart(u);
    u.addEventListener('boundary', (e) => {
      if (e.name && e.name !== 'word') return;
      const word = (spokenText.slice(e.charIndex).match(/^\S+/) || [''])[0];
      if (!lip.gotBoundary) { lip.gotBoundary = true; clearTimeout(lip.fallbackTimer); lip.timeline = []; }
      lipWord(word);
      const end = e.charIndex + (e.charLength || word.length);
      spokenUpTo = Math.max(spokenUpTo, Math.min(spokenText.length, end));
      renderText();
    });
    const finish = () => {
      if (!speaking) return;
      speaking = false;
      spokenUpTo = spokenText.length;
      renderText();
      lipStop();
      // Return to idle unless the page already moved on (e.g. started listening).
      if (wrap.classList.contains('rb-speaking')) setState(state);
    };
    u.addEventListener('end', finish);
    u.addEventListener('error', finish);
  }

  if ('speechSynthesis' in window) {
    const origSpeak = speechSynthesis.speak.bind(speechSynthesis);
    speechSynthesis.speak = (u) => { try { onUtterance(u); } catch {} return origSpeak(u); };
    const origCancel = speechSynthesis.cancel.bind(speechSynthesis);
    speechSynthesis.cancel = () => {
      if (speaking) { speaking = false; spokenUpTo = spokenText.length; renderText(); lipStop(); setState(state); }
      return origCancel();
    };
  }

  window.Robot = { state: setState, say, level, wave, _visemes: wordVisemes, _wordMs: wordDuration };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
})();
