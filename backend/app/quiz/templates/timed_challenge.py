"""Timed Challenge template — countdown timer with fast-paced question flow."""
from __future__ import annotations

from backend.app.quiz.templates.base import SUPABASE_SUBMIT_JS, escape_json_for_html


def render(concept: dict, questions: list[dict], colors: dict) -> str:
    concept_json = escape_json_for_html(concept)
    questions_json = escape_json_for_html(questions)
    time_per_q = 30  # seconds per question

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: {colors['bg']}; color: {colors['text']}; min-height:100vh; padding:16px; }}
.container {{ max-width:640px; margin:0 auto; }}
.timer-bar {{ position:relative; height:6px; background:#e5e7eb; border-radius:3px;
  margin-bottom:16px; overflow:hidden; }}
.timer-fill {{ height:100%; background:{colors['primary']}; border-radius:3px;
  transition:width 1s linear; }}
.timer-fill.warning {{ background:#f59e0b; }}
.timer-fill.danger {{ background:{colors['wrong']}; }}
.header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; }}
.timer-text {{ font-size:1.8em; font-weight:800; font-variant-numeric:tabular-nums;
  color:{colors['primary']}; }}
.timer-text.warning {{ color:#f59e0b; }}
.timer-text.danger {{ color:{colors['wrong']}; animation:pulse 0.5s infinite; }}
@keyframes pulse {{ 0%,100% {{ opacity:1; }} 50% {{ opacity:0.5; }} }}
.counter {{ font-size:0.9em; color:{colors['muted']}; }}
.concept-mini {{ background:{colors['card']}; border-radius:10px; padding:14px;
  margin-bottom:16px; font-size:0.9em; line-height:1.5; cursor:pointer;
  border:1px solid {colors['accent']}30; }}
.concept-mini .toggle {{ color:{colors['primary']}; font-weight:600; }}
.concept-detail {{ display:none; margin-top:8px; }}
.concept-detail.show {{ display:block; }}
.card {{ background:{colors['card']}; border-radius:16px; padding:24px;
  box-shadow:0 4px 20px rgba(0,0,0,0.08); animation:slideUp 0.3s ease; }}
@keyframes slideUp {{ from {{ opacity:0; transform:translateY(20px); }}
  to {{ opacity:1; transform:translateY(0); }} }}
.q-text {{ font-size:1.15em; font-weight:600; margin-bottom:18px; line-height:1.5; }}
.q-type-badge {{ display:inline-block; font-size:0.75em; padding:3px 10px; border-radius:10px;
  background:{colors['primary']}15; color:{colors['primary']}; font-weight:600; margin-bottom:10px; }}
.options {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }}
@media (max-width:480px) {{ .options {{ grid-template-columns:1fr; }} }}
.option {{ padding:14px; border:2px solid #e5e7eb; border-radius:12px; cursor:pointer;
  text-align:center; transition:all 0.15s; font-weight:500; }}
.option:hover {{ border-color:{colors['primary']}; background:{colors['primary']}08; }}
.option.selected {{ border-color:{colors['primary']}; background:{colors['primary']}18;
  font-weight:700; }}
.fill-input {{ width:100%; padding:14px; border:2px solid #e5e7eb; border-radius:12px;
  font-size:1em; outline:none; }}
.fill-input:focus {{ border-color:{colors['primary']}; }}
.skip-btn {{ display:block; margin:12px auto 0; background:none; border:none;
  color:{colors['muted']}; cursor:pointer; font-size:0.9em; }}
.skip-btn:hover {{ color:{colors['text']}; }}
.results {{ text-align:center; animation:slideUp 0.5s; }}
.result-big {{ font-size:3em; font-weight:800; margin:16px 0 4px; }}
.result-time {{ color:{colors['muted']}; margin-bottom:20px; }}
.result-grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(40px,1fr));
  gap:8px; margin:16px 0; }}
.dot {{ width:40px; height:40px; border-radius:50%; display:flex; align-items:center;
  justify-content:center; font-weight:700; font-size:0.85em; color:white; }}
.dot.c {{ background:{colors['correct']}; }}
.dot.w {{ background:{colors['wrong']}; }}
.dot.s {{ background:#d1d5db; color:#6b7280; }}
.detail-list {{ text-align:left; margin-top:16px; }}
.d-item {{ background:{colors['card']}; border-radius:10px; padding:12px; margin:8px 0;
  box-shadow:0 1px 4px rgba(0,0,0,0.05); }}
.d-item.wrong {{ border-left:3px solid {colors['wrong']}; }}
.d-q {{ font-weight:600; font-size:0.95em; }}
.d-exp {{ font-size:0.85em; color:{colors['muted']}; margin-top:4px; }}
#save-status {{ text-align:center; margin-top:10px; font-weight:600; }}
</style>
</head>
<body>
<div class="container">
  <div class="timer-bar"><div class="timer-fill" id="timer-fill" style="width:100%"></div></div>
  <div class="header">
    <div class="timer-text" id="timer-text">{time_per_q}</div>
    <div class="counter" id="counter"></div>
  </div>
  <div class="concept-mini" onclick="document.getElementById('cd').classList.toggle('show')">
    <span class="toggle">📖 Quick Review</span>
    <div class="concept-detail" id="cd"></div>
  </div>
  <div id="quiz-area"></div>
</div>
<script>
{SUPABASE_SUBMIT_JS}

const concept = {concept_json};
const questions = {questions_json};
const TIME_PER_Q = {time_per_q};
let idx = 0, timeLeft = TIME_PER_Q, timerInterval = null;
let totalTime = 0;
const answers = {{}};
const details = [];

(function init() {{
  let cHtml = '<p>' + (concept.explanation || '').substring(0, 200) + '...</p>';
  if (concept.key_points && concept.key_points.length) {{
    cHtml += '<ul style="padding-left:16px;margin-top:6px">';
    concept.key_points.slice(0,3).forEach(p => cHtml += '<li>' + p + '</li>');
    cHtml += '</ul>';
  }}
  document.getElementById('cd').innerHTML = cHtml;
  showQuestion();
}})();

function startTimer() {{
  timeLeft = TIME_PER_Q;
  updateTimerDisplay();
  clearInterval(timerInterval);
  timerInterval = setInterval(() => {{
    timeLeft--;
    totalTime++;
    updateTimerDisplay();
    if (timeLeft <= 0) {{ clearInterval(timerInterval); autoSkip(); }}
  }}, 1000);
}}

function updateTimerDisplay() {{
  const fill = document.getElementById('timer-fill');
  const text = document.getElementById('timer-text');
  const pct = (timeLeft / TIME_PER_Q) * 100;
  fill.style.width = pct + '%';
  text.textContent = timeLeft;
  fill.className = 'timer-fill' + (timeLeft <= 5 ? ' danger' : timeLeft <= 10 ? ' warning' : '');
  text.className = 'timer-text' + (timeLeft <= 5 ? ' danger' : timeLeft <= 10 ? ' warning' : '');
}}

function showQuestion() {{
  document.getElementById('counter').textContent = (idx+1) + ' / ' + questions.length;
  const q = questions[idx];
  const area = document.getElementById('quiz-area');
  const typeLabel = q.type.replace('_',' ');
  let html = '<div class="card"><div class="q-type-badge">' + typeLabel + '</div>'
    + '<div class="q-text">' + q.question + '</div>';
  if (q.type === 'fill_blank') {{
    html += '<input class="fill-input" id="fill-input" placeholder="Type your answer..." '
      + 'onkeydown="if(event.key===\\'Enter\\')lockAnswer()">'
      + '<button class="skip-btn" onclick="lockAnswer()" style="margin-top:10px;background:{colors["primary"]};color:white;padding:10px 24px;border-radius:10px;font-weight:600;cursor:pointer">Confirm</button>';
  }} else {{
    html += '<div class="options">';
    const opts = q.options || (q.type === 'true_false' ? ['True','False'] : []);
    opts.forEach(opt => {{
      html += '<div class="option" onclick="pickAndLock(this,\\'' + opt.replace(/'/g, "\\\\'") + '\\')">' + opt + '</div>';
    }});
    html += '</div>';
  }}
  html += '<button class="skip-btn" onclick="autoSkip()">Skip →</button></div>';
  area.innerHTML = html;
  startTimer();
}}

function pickAndLock(el, val) {{
  answers[questions[idx].id] = val;
  el.parentElement.querySelectorAll('.option').forEach(o => o.classList.remove('selected'));
  el.classList.add('selected');
  setTimeout(() => lockAnswer(), 400);
}}

function lockAnswer() {{
  clearInterval(timerInterval);
  const q = questions[idx];
  const userAns = answers[q.id] || (document.getElementById('fill-input') ? document.getElementById('fill-input').value : '');
  answers[q.id] = userAns;
  let isCorrect = false;
  if (q.type === 'fill_blank') {{
    isCorrect = userAns.trim().toLowerCase() === (q.correct_answer||'').trim().toLowerCase();
  }} else {{
    isCorrect = userAns === q.correct_answer;
  }}
  details.push({{ qid:q.id, selected:userAns, correct:q.correct_answer, isCorrect }});
  idx++;
  if (idx >= questions.length) showResults();
  else showQuestion();
}}

function autoSkip() {{
  clearInterval(timerInterval);
  const q = questions[idx];
  details.push({{ qid:q.id, selected:'', correct:q.correct_answer, isCorrect:false }});
  idx++;
  if (idx >= questions.length) showResults();
  else showQuestion();
}}

function showResults() {{
  clearInterval(timerInterval);
  const score = details.filter(d => d.isCorrect).length;
  const pct = Math.round(score / questions.length * 100);
  const color = pct >= 70 ? '{colors["correct"]}' : pct >= 40 ? '#f59e0b' : '{colors["wrong"]}';
  const mins = Math.floor(totalTime / 60);
  const secs = totalTime % 60;

  let html = '<div class="results">'
    + '<div class="result-big" style="color:' + color + '">' + score + '/' + questions.length + '</div>'
    + '<div class="result-time">Completed in ' + mins + 'm ' + secs + 's</div>'
    + '<div class="result-grid">';
  details.forEach((d,i) => {{
    const cls = d.isCorrect ? 'c' : (d.selected ? 'w' : 's');
    html += '<div class="dot ' + cls + '">' + (i+1) + '</div>';
  }});
  html += '</div><div class="detail-list">';
  details.forEach((d,i) => {{
    if (!d.isCorrect) {{
      const q = questions[i];
      html += '<div class="d-item wrong"><div class="d-q">Q' + (i+1) + ': ' + q.question + '</div>'
        + '<div class="d-exp">Your answer: ' + (d.selected || 'skipped') + ' — Correct: ' + d.correct
        + (q.explanation ? '<br>' + q.explanation : '') + '</div></div>';
    }}
  }});
  html += '</div><div id="save-status"></div></div>';
  document.getElementById('quiz-area').innerHTML = html;
  document.getElementById('timer-fill').style.width = '100%';
  document.getElementById('timer-fill').className = 'timer-fill';
  document.getElementById('timer-text').textContent = '✓';
  document.getElementById('timer-text').className = 'timer-text';
  submitResults('{{{{QUIZ_ID}}}}', score, questions.length, details);
}}
</script>
</body>
</html>"""
