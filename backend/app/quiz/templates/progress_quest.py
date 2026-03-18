"""Progress Quest template — gamified with stars and a character advancing."""
from __future__ import annotations

from backend.app.quiz.templates.base import SUPABASE_SUBMIT_JS, escape_json_for_html


def render(concept: dict, questions: list[dict], colors: dict) -> str:
    concept_json = escape_json_for_html(concept)
    questions_json = escape_json_for_html(questions)

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: {colors['bg']}; color: {colors['text']}; min-height:100vh; padding:16px; }}
.container {{ max-width:640px; margin:0 auto; }}
.quest-header {{ text-align:center; margin-bottom:16px; }}
.quest-title {{ font-size:1.4em; font-weight:800; color:{colors['primary']}; }}
.stars {{ font-size:1.8em; margin:8px 0; letter-spacing:4px; }}
.star {{ opacity:0.2; transition:all 0.5s; display:inline-block; }}
.star.earned {{ opacity:1; transform:scale(1.3); }}
.track {{ background:{colors['card']}; border-radius:20px; height:60px; margin:12px 0 20px;
  position:relative; overflow:hidden; box-shadow:inset 0 2px 8px rgba(0,0,0,0.1); }}
.track-fill {{ height:100%; background: linear-gradient(90deg, {colors['accent']}40, {colors['primary']});
  border-radius:20px; transition:width 0.6s ease; position:relative; }}
.runner {{ position:absolute; right:-10px; top:50%; transform:translateY(-50%);
  font-size:2em; transition:all 0.4s; }}
.concept-btn {{ display:block; width:100%; padding:12px; background:{colors['card']};
  border:2px solid {colors['accent']}40; border-radius:12px; cursor:pointer; text-align:left;
  font-size:0.95em; color:{colors['primary']}; font-weight:600; margin-bottom:16px; }}
.concept-btn:hover {{ border-color:{colors['primary']}; }}
.concept-panel {{ display:none; background:{colors['card']}; border-radius:12px; padding:16px;
  margin-bottom:16px; box-shadow:0 2px 10px rgba(0,0,0,0.06); line-height:1.6; }}
.concept-panel.show {{ display:block; animation:fadeIn 0.3s; }}
.concept-panel ul {{ padding-left:18px; margin:8px 0; }}
.concept-panel li {{ margin:4px 0; }}
@keyframes fadeIn {{ from {{ opacity:0; }} to {{ opacity:1; }} }}
.card {{ background:{colors['card']}; border-radius:16px; padding:24px;
  box-shadow:0 4px 16px rgba(0,0,0,0.08); animation:popIn 0.35s ease; }}
@keyframes popIn {{ from {{ opacity:0; transform:scale(0.95); }}
  to {{ opacity:1; transform:scale(1); }} }}
.q-label {{ font-size:0.8em; color:{colors['muted']}; text-transform:uppercase;
  letter-spacing:1px; margin-bottom:6px; }}
.q-text {{ font-size:1.1em; font-weight:600; margin:10px 0 18px; line-height:1.5; }}
.options {{ display:flex; flex-direction:column; gap:10px; }}
.option {{ padding:14px 18px; border:2px solid #e5e7eb; border-radius:12px; cursor:pointer;
  transition:all 0.2s; }}
.option:hover {{ border-color:{colors['primary']}; }}
.option.selected {{ border-color:{colors['primary']}; background:{colors['primary']}15; font-weight:600; }}
.option.correct-reveal {{ border-color:{colors['correct']}; background:{colors['correct']}15; }}
.option.wrong-reveal {{ border-color:{colors['wrong']}; background:{colors['wrong']}10; text-decoration:line-through; opacity:0.7; }}
.fill-input {{ width:100%; padding:14px; border:2px solid #e5e7eb; border-radius:12px;
  font-size:1em; outline:none; }}
.fill-input:focus {{ border-color:{colors['primary']}; }}
.feedback-inline {{ margin-top:14px; padding:12px; border-radius:10px; font-size:0.95em;
  animation:fadeIn 0.4s; }}
.feedback-inline.correct {{ background:#f0fdf4; color:#166534; border:1px solid #bbf7d0; }}
.feedback-inline.wrong {{ background:#fef2f2; color:#991b1b; border:1px solid #fecaca; }}
.nav {{ display:flex; justify-content:flex-end; margin-top:16px; }}
.btn {{ padding:12px 32px; border-radius:12px; border:none; font-size:1em; cursor:pointer;
  font-weight:700; background:{colors['primary']}; color:white; transition:all 0.2s; }}
.btn:hover {{ filter:brightness(1.1); transform:translateY(-1px); }}
.results {{ text-align:center; animation:popIn 0.5s; }}
.trophy {{ font-size:4em; margin:10px 0; }}
.score-text {{ font-size:2.2em; font-weight:800; }}
.score-sub {{ color:{colors['muted']}; margin-bottom:20px; }}
.result-list {{ text-align:left; }}
.r-item {{ padding:12px; margin:8px 0; border-radius:10px; background:{colors['card']};
  box-shadow:0 1px 4px rgba(0,0,0,0.05); }}
.r-item.correct {{ border-left:4px solid {colors['correct']}; }}
.r-item.wrong {{ border-left:4px solid {colors['wrong']}; }}
.r-q {{ font-weight:600; font-size:0.95em; }}
.r-exp {{ font-size:0.85em; color:{colors['muted']}; margin-top:4px; }}
#save-status {{ text-align:center; margin-top:10px; font-weight:600; }}
</style>
</head>
<body>
<div class="container">
  <div class="quest-header">
    <div class="quest-title">Knowledge Quest</div>
    <div class="stars" id="stars"></div>
  </div>
  <div class="track"><div class="track-fill" id="track-fill" style="width:0%">
    <span class="runner" id="runner">🏃</span></div></div>
  <button class="concept-btn" onclick="toggleConcept()">📖 Read the concept first</button>
  <div class="concept-panel" id="concept-panel"></div>
  <div id="quiz-area"></div>
</div>
<script>
{SUPABASE_SUBMIT_JS}

const concept = {concept_json};
const questions = {questions_json};
let idx = 0, score = 0, answered = false;
const userAnswers = {{}};
const details = [];

(function init() {{
  let s = '';
  for (let i = 0; i < questions.length; i++) s += '<span class="star" id="star-'+i+'">⭐</span>';
  document.getElementById('stars').innerHTML = s;

  let cHtml = '<p>' + (concept.explanation || '') + '</p>';
  if (concept.key_points && concept.key_points.length) {{
    cHtml += '<ul>';
    concept.key_points.forEach(p => cHtml += '<li>' + p + '</li>');
    cHtml += '</ul>';
  }}
  if (concept.example) cHtml += '<p style="margin-top:10px;font-style:italic"><strong>Example:</strong> ' + concept.example + '</p>';
  document.getElementById('concept-panel').innerHTML = cHtml;

  showQuestion();
}})();

function toggleConcept() {{
  document.getElementById('concept-panel').classList.toggle('show');
}}

function updateTrack() {{
  const pct = (idx / questions.length) * 100;
  document.getElementById('track-fill').style.width = pct + '%';
}}

function showQuestion() {{
  answered = false;
  updateTrack();
  const q = questions[idx];
  const area = document.getElementById('quiz-area');
  const typeLabel = q.type.replace('_',' ');
  let html = '<div class="card"><div class="q-label">Question ' + (idx+1) + ' of ' + questions.length + ' &middot; ' + typeLabel + '</div>'
    + '<div class="q-text">' + q.question + '</div><div class="options" id="opts">';
  if (q.type === 'fill_blank') {{
    html += '<input class="fill-input" id="fill-input" placeholder="Type your answer...">';
    html += '</div><div class="nav"><button class="btn" id="check-btn" onclick="checkFill()">Check</button></div>';
  }} else {{
    const opts = q.options || (q.type === 'true_false' ? ['True','False'] : []);
    opts.forEach(opt => {{
      html += '<div class="option" onclick="checkOption(this,\\'' + opt.replace(/'/g, "\\\\'") + '\\')">' + opt + '</div>';
    }});
    html += '</div>';
  }}
  html += '<div id="feedback-slot"></div></div>';
  area.innerHTML = html;
}}

function checkOption(el, val) {{
  if (answered) return;
  answered = true;
  const q = questions[idx];
  const isCorrect = val === q.correct_answer;
  userAnswers[q.id] = val;
  if (isCorrect) score++;
  details.push({{ qid:q.id, selected:val, correct:q.correct_answer, isCorrect }});

  el.parentElement.querySelectorAll('.option').forEach(o => {{
    const oText = o.textContent;
    if (oText === q.correct_answer) o.classList.add('correct-reveal');
    else if (o === el && !isCorrect) o.classList.add('wrong-reveal');
  }});
  if (isCorrect) document.getElementById('star-'+idx).classList.add('earned');
  showFeedback(isCorrect, q);
  setTimeout(() => advance(), 1800);
}}

function checkFill() {{
  if (answered) return;
  answered = true;
  const q = questions[idx];
  const val = document.getElementById('fill-input').value;
  const isCorrect = val.trim().toLowerCase() === (q.correct_answer||'').trim().toLowerCase();
  userAnswers[q.id] = val;
  if (isCorrect) {{ score++; document.getElementById('star-'+idx).classList.add('earned'); }}
  details.push({{ qid:q.id, selected:val, correct:q.correct_answer, isCorrect }});
  showFeedback(isCorrect, q);
  setTimeout(() => advance(), 1800);
}}

function showFeedback(ok, q) {{
  const slot = document.getElementById('feedback-slot');
  const cls = ok ? 'correct' : 'wrong';
  const icon = ok ? '&#10003; Correct!' : '&#10007; The answer is: <strong>' + q.correct_answer + '</strong>';
  slot.innerHTML = '<div class="feedback-inline ' + cls + '">' + icon + '</div>';
}}

function advance() {{
  idx++;
  if (idx >= questions.length) showResults();
  else showQuestion();
}}

function showResults() {{
  document.getElementById('track-fill').style.width = '100%';
  const pct = Math.round(score / questions.length * 100);
  const trophy = pct >= 80 ? '🏆' : pct >= 50 ? '🥈' : '💪';
  let html = '<div class="results"><div class="trophy">' + trophy + '</div>'
    + '<div class="score-text" style="color:{colors["primary"]}">' + score + ' / ' + questions.length + '</div>'
    + '<div class="score-sub">' + pct + '% — ';
  if (pct >= 80) html += 'Excellent!';
  else if (pct >= 50) html += 'Good effort!';
  else html += 'Keep practicing!';
  html += '</div><div class="result-list">';
  details.forEach((d,i) => {{
    const q = questions[i];
    const cls = d.isCorrect ? 'correct' : 'wrong';
    const icon = d.isCorrect ? '&#10003;' : '&#10007;';
    html += '<div class="r-item ' + cls + '"><div class="r-q">' + icon + ' ' + q.question + '</div>';
    if (!d.isCorrect && q.explanation) html += '<div class="r-exp">' + q.explanation + '</div>';
    html += '</div>';
  }});
  html += '</div><div id="save-status"></div></div>';
  document.getElementById('quiz-area').innerHTML = html;
  submitResults('{{{{QUIZ_ID}}}}', score, questions.length, details);
}}
</script>
</body>
</html>"""
