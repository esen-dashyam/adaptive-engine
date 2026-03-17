"""Card Flip quiz template — one question at a time with flip animation."""
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
.concept-box {{ background:{colors['card']}; border-radius:16px; padding:20px;
  box-shadow:0 2px 12px rgba(0,0,0,0.08); margin-bottom:20px; }}
.concept-box h2 {{ color:{colors['primary']}; margin-bottom:12px; font-size:1.3em; }}
.concept-toggle {{ cursor:pointer; color:{colors['primary']}; font-weight:600;
  display:flex; align-items:center; gap:6px; user-select:none; }}
.concept-toggle::before {{ content:'▶'; font-size:0.7em; transition:transform 0.3s; }}
.concept-toggle.open::before {{ transform:rotate(90deg); }}
.concept-content {{ max-height:0; overflow:hidden; transition:max-height 0.4s ease; }}
.concept-content.open {{ max-height:600px; }}
.concept-content p {{ margin:10px 0; line-height:1.6; }}
.key-points {{ background:{colors['bg']}; border-radius:8px; padding:12px; margin:10px 0; }}
.key-points li {{ margin:4px 0; }}
.example-box {{ background:{colors['bg']}; border-left:3px solid {colors['primary']};
  padding:10px 14px; border-radius:0 8px 8px 0; margin:10px 0; font-style:italic; }}
.progress {{ display:flex; align-items:center; gap:10px; margin-bottom:16px; }}
.progress-bar {{ flex:1; height:8px; background:#e5e7eb; border-radius:4px; overflow:hidden; }}
.progress-fill {{ height:100%; background:{colors['primary']}; border-radius:4px;
  transition:width 0.4s ease; }}
.progress-text {{ font-size:0.9em; color:{colors['muted']}; white-space:nowrap; }}
.card {{ background:{colors['card']}; border-radius:16px; padding:24px;
  box-shadow:0 4px 20px rgba(0,0,0,0.1); animation:slideIn 0.4s ease; }}
@keyframes slideIn {{ from {{ opacity:0; transform:translateX(40px); }}
  to {{ opacity:1; transform:translateX(0); }} }}
.q-number {{ font-size:0.85em; color:{colors['muted']}; text-transform:uppercase;
  letter-spacing:1px; margin-bottom:8px; }}
.q-type {{ display:inline-block; background:{colors['primary']}20; color:{colors['primary']};
  padding:2px 10px; border-radius:12px; font-size:0.75em; font-weight:600; margin-left:8px; }}
.q-text {{ font-size:1.15em; font-weight:600; margin:12px 0 20px; line-height:1.5; }}
.options {{ display:flex; flex-direction:column; gap:10px; }}
.option {{ padding:14px 18px; border:2px solid #e5e7eb; border-radius:12px; cursor:pointer;
  transition:all 0.2s; font-size:1em; background:{colors['card']}; }}
.option:hover {{ border-color:{colors['primary']}; background:{colors['primary']}08; }}
.option.selected {{ border-color:{colors['primary']}; background:{colors['primary']}15;
  font-weight:600; }}
.fill-input {{ width:100%; padding:14px; border:2px solid #e5e7eb; border-radius:12px;
  font-size:1em; outline:none; transition:border-color 0.2s; }}
.fill-input:focus {{ border-color:{colors['primary']}; }}
.hint {{ font-size:0.85em; color:{colors['muted']}; margin-top:10px; cursor:pointer; }}
.hint-text {{ display:none; margin-top:6px; padding:8px 12px; background:{colors['bg']};
  border-radius:8px; font-size:0.9em; }}
.nav {{ display:flex; justify-content:space-between; margin-top:20px; }}
.btn {{ padding:12px 28px; border-radius:12px; border:none; font-size:1em; cursor:pointer;
  font-weight:600; transition:all 0.2s; }}
.btn-primary {{ background:{colors['primary']}; color:white; }}
.btn-primary:hover {{ filter:brightness(1.1); transform:translateY(-1px); }}
.btn-secondary {{ background:#e5e7eb; color:{colors['text']}; }}
.btn-secondary:hover {{ background:#d1d5db; }}
.btn:disabled {{ opacity:0.4; cursor:not-allowed; transform:none; }}
.results {{ text-align:center; animation:slideIn 0.5s ease; }}
.score-circle {{ width:140px; height:140px; border-radius:50%; margin:20px auto;
  display:flex; flex-direction:column; align-items:center; justify-content:center;
  font-size:2.5em; font-weight:800; color:white;
  box-shadow:0 8px 30px rgba(0,0,0,0.15); }}
.score-label {{ font-size:0.35em; font-weight:400; opacity:0.9; }}
.result-item {{ background:{colors['card']}; border-radius:12px; padding:16px;
  margin:12px 0; text-align:left; box-shadow:0 1px 4px rgba(0,0,0,0.06); }}
.result-item.correct {{ border-left:4px solid {colors['correct']}; }}
.result-item.wrong {{ border-left:4px solid {colors['wrong']}; }}
.result-q {{ font-weight:600; margin-bottom:6px; }}
.result-answer {{ font-size:0.9em; color:{colors['muted']}; }}
.result-explanation {{ font-size:0.85em; margin-top:6px; padding:8px;
  background:{colors['bg']}; border-radius:8px; }}
#save-status {{ margin-top:10px; font-size:0.9em; font-weight:600; }}
</style>
</head>
<body>
<div class="container">
  <div class="concept-box">
    <div class="concept-toggle" onclick="toggleConcept(this)">Learn the Concept</div>
    <div class="concept-content" id="concept-content"></div>
  </div>
  <div class="progress">
    <div class="progress-bar"><div class="progress-fill" id="progress-fill"></div></div>
    <div class="progress-text" id="progress-text"></div>
  </div>
  <div id="quiz-area"></div>
</div>
<script>
{SUPABASE_SUBMIT_JS}

const concept = {concept_json};
const questions = {questions_json};
let current = 0;
let answers = {{}};

function toggleConcept(el) {{
  el.classList.toggle('open');
  document.getElementById('concept-content').classList.toggle('open');
}}

function initConcept() {{
  const c = document.getElementById('concept-content');
  let html = '<p>' + (concept.explanation || '') + '</p>';
  if (concept.key_points && concept.key_points.length) {{
    html += '<div class="key-points"><strong>Key Points:</strong><ul>';
    concept.key_points.forEach(p => html += '<li>' + p + '</li>');
    html += '</ul></div>';
  }}
  if (concept.example) {{
    html += '<div class="example-box"><strong>Example:</strong> ' + concept.example + '</div>';
  }}
  c.innerHTML = html;
}}

function updateProgress() {{
  const pct = ((current + 1) / questions.length) * 100;
  document.getElementById('progress-fill').style.width = pct + '%';
  document.getElementById('progress-text').textContent = (current + 1) + ' / ' + questions.length;
}}

function renderQuestion() {{
  const q = questions[current];
  const area = document.getElementById('quiz-area');
  updateProgress();

  let optionsHtml = '';
  if (q.type === 'fill_blank') {{
    const val = answers[q.id] || '';
    optionsHtml = '<input class="fill-input" id="fill-input" placeholder="Type your answer..."'
      + ' value="' + val + '" oninput="answers[' + q.id + ']=this.value">';
  }} else {{
    const opts = q.options || (q.type === 'true_false' ? ['True', 'False'] : []);
    opts.forEach((opt, i) => {{
      const sel = answers[q.id] === opt ? ' selected' : '';
      optionsHtml += '<div class="option' + sel + '" onclick="selectOption(' + q.id + ',this,\\'' + opt.replace(/'/g, "\\\\'") + '\\')">' + opt + '</div>';
    }});
  }}

  const hintHtml = q.hint ? '<div class="hint" onclick="this.querySelector(\\'.hint-text\\').style.display=\\'block\\'">Need a hint?<div class="hint-text">' + q.hint + '</div></div>' : '';

  const typeLabel = q.type.replace('_', ' ');
  area.innerHTML = '<div class="card">'
    + '<div class="q-number">Question ' + (current + 1) + '<span class="q-type">' + typeLabel + '</span></div>'
    + '<div class="q-text">' + q.question + '</div>'
    + '<div class="options">' + optionsHtml + '</div>'
    + hintHtml
    + '<div class="nav">'
    + (current > 0 ? '<button class="btn btn-secondary" onclick="prev()">Back</button>' : '<div></div>')
    + (current < questions.length - 1
        ? '<button class="btn btn-primary" onclick="next()">Next</button>'
        : '<button class="btn btn-primary" onclick="submitQuiz()">Submit Quiz</button>')
    + '</div></div>';
}}

function selectOption(qId, el, val) {{
  answers[qId] = val;
  el.parentElement.querySelectorAll('.option').forEach(o => o.classList.remove('selected'));
  el.classList.add('selected');
}}

function prev() {{ if (current > 0) {{ current--; renderQuestion(); }} }}
function next() {{ current++; renderQuestion(); }}

function submitQuiz() {{
  let score = 0;
  const details = [];
  questions.forEach(q => {{
    const userAns = answers[q.id] || '';
    let isCorrect = false;
    if (q.type === 'fill_blank') {{
      isCorrect = userAns.trim().toLowerCase() === (q.correct_answer || '').trim().toLowerCase();
    }} else {{
      isCorrect = userAns === q.correct_answer;
    }}
    if (isCorrect) score++;
    details.push({{ qid: q.id, selected: userAns, correct: q.correct_answer, isCorrect: isCorrect }});
  }});
  showResults(score, details);
  submitResults('{{{{QUIZ_ID}}}}', score, questions.length, details);
}}

function showResults(score, details) {{
  const pct = Math.round(score / questions.length * 100);
  const bg = pct >= 70 ? '{colors["correct"]}' : pct >= 40 ? '#f59e0b' : '{colors["wrong"]}';
  document.getElementById('progress-fill').style.width = '100%';
  document.getElementById('progress-text').textContent = 'Done!';

  let html = '<div class="results">'
    + '<div class="score-circle" style="background:' + bg + '">'
    + score + '/' + questions.length + '<div class="score-label">' + pct + '%</div></div>';

  details.forEach((d, i) => {{
    const q = questions[i];
    const cls = d.isCorrect ? 'correct' : 'wrong';
    const icon = d.isCorrect ? '&#10003;' : '&#10007;';
    html += '<div class="result-item ' + cls + '">'
      + '<div class="result-q">' + icon + ' Q' + (i+1) + ': ' + q.question + '</div>'
      + '<div class="result-answer">Your answer: ' + (d.selected || '<em>skipped</em>') + '</div>';
    if (!d.isCorrect) {{
      html += '<div class="result-answer" style="color:{colors["correct"]}">Correct: ' + d.correct + '</div>';
      if (q.explanation) html += '<div class="result-explanation">' + q.explanation + '</div>';
    }}
    html += '</div>';
  }});
  html += '<div id="save-status"></div></div>';
  document.getElementById('quiz-area').innerHTML = html;
}}

initConcept();
renderQuestion();
</script>
</body>
</html>"""
