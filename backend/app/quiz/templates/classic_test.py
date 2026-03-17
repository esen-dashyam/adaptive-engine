"""Classic Test template — all questions displayed on one page."""
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
.container {{ max-width:680px; margin:0 auto; }}
h1 {{ text-align:center; color:{colors['primary']}; margin:12px 0 4px; font-size:1.5em; }}
.subtitle {{ text-align:center; color:{colors['muted']}; margin-bottom:20px; font-size:0.9em; }}
.concept-card {{ background:{colors['card']}; border-radius:14px; padding:20px;
  box-shadow:0 2px 10px rgba(0,0,0,0.06); margin-bottom:24px;
  border-top:4px solid {colors['primary']}; }}
.concept-card h3 {{ color:{colors['primary']}; margin-bottom:10px; }}
.concept-card p {{ line-height:1.6; margin:8px 0; }}
.concept-card ul {{ padding-left:20px; margin:8px 0; }}
.concept-card li {{ margin:4px 0; }}
.example {{ background:{colors['bg']}; padding:12px; border-radius:8px; margin:10px 0;
  border-left:3px solid {colors['accent']}; font-style:italic; }}
.question {{ background:{colors['card']}; border-radius:14px; padding:20px; margin-bottom:16px;
  box-shadow:0 2px 8px rgba(0,0,0,0.05); transition:border-color 0.3s; border:2px solid transparent; }}
.question.answered {{ border-color:{colors['accent']}30; }}
.q-header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; }}
.q-num {{ font-weight:700; color:{colors['primary']}; }}
.q-badge {{ font-size:0.75em; padding:3px 10px; border-radius:10px;
  background:{colors['primary']}15; color:{colors['primary']}; font-weight:600; }}
.q-text {{ font-size:1.05em; font-weight:500; margin-bottom:14px; line-height:1.5; }}
.opts {{ display:flex; flex-direction:column; gap:8px; }}
.opt {{ padding:12px 16px; border:2px solid #e5e7eb; border-radius:10px; cursor:pointer;
  transition:all 0.15s; }}
.opt:hover {{ border-color:{colors['primary']}; background:{colors['primary']}05; }}
.opt.sel {{ border-color:{colors['primary']}; background:{colors['primary']}12; font-weight:600; }}
.fill {{ width:100%; padding:12px; border:2px solid #e5e7eb; border-radius:10px;
  font-size:1em; outline:none; }}
.fill:focus {{ border-color:{colors['primary']}; }}
.submit-area {{ text-align:center; margin:24px 0; }}
.submit-btn {{ padding:14px 48px; background:{colors['primary']}; color:white; border:none;
  border-radius:14px; font-size:1.1em; font-weight:700; cursor:pointer;
  transition:all 0.2s; box-shadow:0 4px 14px {colors['primary']}40; }}
.submit-btn:hover {{ transform:translateY(-2px); filter:brightness(1.1); }}
.submit-btn:disabled {{ opacity:0.5; cursor:not-allowed; transform:none; }}
.results-header {{ text-align:center; margin:20px 0; }}
.big-score {{ font-size:3em; font-weight:800; }}
.pct {{ font-size:1.2em; color:{colors['muted']}; }}
.question.correct {{ border-color:{colors['correct']}; }}
.question.wrong {{ border-color:{colors['wrong']}; }}
.feedback {{ margin-top:10px; padding:10px; border-radius:8px; font-size:0.9em; }}
.feedback.correct {{ background:#f0fdf4; color:#166534; }}
.feedback.wrong {{ background:#fef2f2; color:#991b1b; }}
#save-status {{ text-align:center; margin-top:8px; font-weight:600; }}
</style>
</head>
<body>
<div class="container">
  <h1 id="quiz-title"></h1>
  <div class="subtitle" id="quiz-subtitle"></div>
  <div class="concept-card" id="concept-area"></div>
  <div id="questions-area"></div>
  <div class="submit-area" id="submit-area">
    <button class="submit-btn" onclick="gradeQuiz()">Submit All Answers</button>
  </div>
  <div id="results-area"></div>
  <div id="save-status"></div>
</div>
<script>
{SUPABASE_SUBMIT_JS}

const concept = {concept_json};
const questions = {questions_json};
const answers = {{}};

(function init() {{
  document.getElementById('quiz-title').textContent = concept.title || 'Practice Quiz';
  document.getElementById('quiz-subtitle').textContent = questions.length + ' questions';

  let cHtml = '<h3>' + (concept.title || '') + '</h3>'
    + '<p>' + (concept.explanation || '') + '</p>';
  if (concept.key_points && concept.key_points.length) {{
    cHtml += '<ul>';
    concept.key_points.forEach(p => cHtml += '<li>' + p + '</li>');
    cHtml += '</ul>';
  }}
  if (concept.example) cHtml += '<div class="example"><strong>Example:</strong> ' + concept.example + '</div>';
  document.getElementById('concept-area').innerHTML = cHtml;

  let qHtml = '';
  questions.forEach((q, i) => {{
    const typeLabel = q.type.replace('_', ' ');
    qHtml += '<div class="question" id="q-' + q.id + '">'
      + '<div class="q-header"><span class="q-num">Q' + (i+1) + '</span>'
      + '<span class="q-badge">' + typeLabel + '</span></div>'
      + '<div class="q-text">' + q.question + '</div>';
    if (q.type === 'fill_blank') {{
      qHtml += '<input class="fill" placeholder="Your answer..." oninput="setAnswer(' + q.id + ',this.value,' + q.id + ')">';
    }} else {{
      const opts = q.options || (q.type === 'true_false' ? ['True','False'] : []);
      qHtml += '<div class="opts">';
      opts.forEach(opt => {{
        qHtml += '<div class="opt" onclick="pickOpt(' + q.id + ',this,\\'' + opt.replace(/'/g, "\\\\'") + '\\')">' + opt + '</div>';
      }});
      qHtml += '</div>';
    }}
    qHtml += '<div class="feedback" id="fb-' + q.id + '" style="display:none"></div></div>';
  }});
  document.getElementById('questions-area').innerHTML = qHtml;
}})();

function pickOpt(qId, el, val) {{
  answers[qId] = val;
  el.parentElement.querySelectorAll('.opt').forEach(o => o.classList.remove('sel'));
  el.classList.add('sel');
  document.getElementById('q-' + qId).classList.add('answered');
}}
function setAnswer(qId, val) {{
  answers[qId] = val;
  if (val.trim()) document.getElementById('q-' + qId).classList.add('answered');
}}

function gradeQuiz() {{
  let score = 0;
  const details = [];
  questions.forEach(q => {{
    const userAns = answers[q.id] || '';
    let isCorrect = false;
    if (q.type === 'fill_blank') {{
      isCorrect = userAns.trim().toLowerCase() === (q.correct_answer||'').trim().toLowerCase();
    }} else {{
      isCorrect = userAns === q.correct_answer;
    }}
    if (isCorrect) score++;
    details.push({{ qid:q.id, selected:userAns, correct:q.correct_answer, isCorrect }});

    const qEl = document.getElementById('q-' + q.id);
    qEl.classList.add(isCorrect ? 'correct' : 'wrong');
    const fb = document.getElementById('fb-' + q.id);
    fb.style.display = 'block';
    fb.className = 'feedback ' + (isCorrect ? 'correct' : 'wrong');
    if (isCorrect) {{
      fb.innerHTML = '&#10003; Correct!';
    }} else {{
      fb.innerHTML = '&#10007; Incorrect. Answer: <strong>' + q.correct_answer + '</strong>'
        + (q.explanation ? '<br>' + q.explanation : '');
    }}
  }});
  const pct = Math.round(score / questions.length * 100);
  const color = pct >= 70 ? '{colors["correct"]}' : pct >= 40 ? '#f59e0b' : '{colors["wrong"]}';
  document.getElementById('submit-area').style.display = 'none';
  document.getElementById('results-area').innerHTML = '<div class="results-header">'
    + '<div class="big-score" style="color:' + color + '">' + score + '/' + questions.length + '</div>'
    + '<div class="pct">' + pct + '%</div></div>';
  window.scrollTo({{ top:0, behavior:'smooth' }});
  submitResults('{{{{QUIZ_ID}}}}', score, questions.length, details);
}}
</script>
</body>
</html>"""
