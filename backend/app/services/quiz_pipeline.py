"""Quiz generation pipeline — orchestrates concept, video, image, animation, questions.

This replaces the LangGraph-based quiz_graph from Calendar Scheduler with
a simple async orchestrator. The pipeline steps are the same:
  1. generate_concept    → Gemini explains the topic
  2. search_video        → YouTube finds best educational video
  3. generate_image      → Imagen creates concept illustration
  4. generate_animation  → Gemini produces Canvas animation scene script
  5. generate_questions  → Gemini produces quiz questions
  6. quality_check       → validates questions (retries up to 2x)
  7. build_html          → assembles two-phase learn+quiz HTML page
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from google import genai
from google.genai import types

from backend.app.core.settings import settings

log = logging.getLogger(__name__)

MAX_RETRIES = 2


# ── Gemini helpers ────────────────────────────────────────


def _gemini_json(prompt: str, temperature: float = 0.8) -> dict:
    """Call Gemini and parse JSON response."""
    client = genai.Client(api_key=settings.gemini_api_key)
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=temperature,
            response_mime_type="application/json",
        ),
    )
    text = resp.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
    return json.loads(text)


def _gemini_text(prompt: str, temperature: float = 0.9) -> str:
    """Call Gemini and return raw text."""
    client = genai.Client(api_key=settings.gemini_api_key)
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=temperature,
        ),
    )
    return resp.text.strip()


# ── Pipeline steps ────────────────────────────────────────


def step_generate_concept(topic: str, subject: str, grade: int, course_title: str, difficulty: str) -> dict:
    """Gemini generates concept explanation."""
    prompt = f"""Explain the concept "{topic}" for a Grade {grade} student
studying {subject} ({course_title}).
Difficulty: {difficulty}.

Return a JSON object:
{{
  "title": "concise title for the concept",
  "explanation": "2-3 paragraph explanation using age-appropriate language and analogies",
  "key_points": ["point 1", "point 2", "point 3"],
  "example": "A worked example demonstrating the concept"
}}

Use simple language. Include a relatable analogy."""
    try:
        return _gemini_json(prompt)
    except Exception as e:
        log.warning("generate_concept failed: %s", e)
        return {
            "title": topic,
            "explanation": f"Let's learn about {topic}.",
            "key_points": [topic],
            "example": "",
        }


def step_search_video(topic: str, grade: int, subject: str) -> dict:
    """Search YouTube for a relevant educational video."""
    try:
        from backend.app.services.youtube_search import search_edu_videos
        results = search_edu_videos(topic=topic, grade=grade, subject=subject, top_n=1)
        return results[0] if results else {}
    except Exception as e:
        log.warning("search_video failed: %s", e)
        return {}


def step_generate_image(topic: str, subject: str, grade: int) -> str:
    """Generate a concept illustration via Imagen."""
    try:
        from backend.app.services.image_gen import generate_concept_image
        return generate_concept_image(topic=topic, subject=subject, grade=grade) or ""
    except Exception as e:
        log.warning("generate_image failed: %s", e)
        return ""


def step_generate_animation(topic: str, subject: str, grade: int, concept: dict) -> str:
    """Gemini generates a JSON scene script; animation engine renders it."""
    from backend.app.quiz.animation_engine import build_animation_html

    concept_json = json.dumps(concept, indent=2)

    prompt = f"""You are a cartoon storyboard artist. Create a short animated lesson
(3-5 scenes, 15-25 seconds total) that teaches "{topic}" to a Grade {grade}
{subject} student, like a fun Flash cartoon for kids.

Concept data:
{concept_json}

Output a JSON array of scenes. Each scene has a duration and visual elements.
Our animation engine will render them — you just describe WHAT to show.

AVAILABLE ELEMENT TYPES and their properties:
- "pie"       → pie chart: x, y, r, slices (int), highlight (array of slice indices),
                fill, hlFill, label, enter, delay
- "fractionBar" → bar divided into parts: x, y, w, h, parts (int), highlight (indices),
                   fill, hlFill, label, enter, delay
- "circle"    → circle: x, y, r, fill, stroke, label, labelColor, enter, delay
- "rect"      → rectangle: x, y, w, h, fill, radius, label, labelColor, enter, delay
- "text"      → text: x, y, text, size (fraction), fill, bold, enter, delay
- "speechBubble" → bubble with text: x, y, w, h, text, fill, textColor, tail ("down"/"none"), enter, delay
- "arrow"     → arrow: x, y, x2, y2, fill, lineWidth, enter, delay
- "star"      → decorative star: x, y, r, fill, enter, delay
- "numberLine" → number line: x, y, w, min, max, marks (array of {{value, label, color}}), enter, delay
- "confetti"  → confetti burst: x, y, count, delay

POSITIONS: x, y are fractions of canvas (0.0 to 1.0). x=0.5,y=0.5 = center.
SIZES: r, w, h, size are also fractions. r=0.1 is 10% of canvas width.
ENTER ANIMATIONS: "slideLeft", "slideRight", "slideUp", "bounceIn", "pop", "scaleIn", "fadeIn"
EASING: add "easing": "bounce"/"elastic"/"back"/"easeOut"/"easeInOut" to any element
DELAY: seconds after scene start before element appears
WOBBLE: add "wobble": true for continuous wiggle after entering

RULES:
- Output ONLY the JSON array. No markdown fences, no explanation.
- 3-5 scenes. Each scene 3-6 seconds.
- Use bright, fun colors. This is for kids!
- Tell a visual STORY — don't just show text. Use pies, bars, shapes, arrows.
- Last scene should be celebratory (confetti, stars, big result text).
- Every scene needs a speechBubble explaining what's happening.
- Use varied enter animations and easing for energy and personality."""

    try:
        scene_data = _gemini_json(prompt, temperature=0.85)
        if isinstance(scene_data, dict) and "scenes" in scene_data:
            scene_data = scene_data["scenes"]
        if not isinstance(scene_data, list) or len(scene_data) < 2:
            log.warning("Scene data invalid, using fallback")
            return _fallback_animation(concept)
        return build_animation_html(scene_data)
    except Exception as e:
        log.warning("generate_animation failed: %s", e)
        return _fallback_animation(concept)


def _fallback_animation(concept: dict) -> str:
    """Simple CSS-animated fallback when Gemini animation fails."""
    title = concept.get("title", "")
    points = concept.get("key_points", [])
    example = concept.get("example", "")
    points_html = "".join(
        f'<li style="animation:fadeSlide 0.5s ease {i*0.2}s both">{p}</li>'
        for i, p in enumerate(points)
    )
    return f"""<div id="animation-root">
<style>
#animation-root {{ font-family: -apple-system, sans-serif; padding: 20px;
  max-width: 600px; margin: 0 auto; }}
#animation-root h3 {{ font-size: 1.3em; margin-bottom: 12px; }}
#animation-root ul {{ padding-left: 20px; }}
#animation-root li {{ margin: 8px 0; opacity: 0; }}
#animation-root .example {{ background: #f0f9ff; border-left: 3px solid #3b82f6;
  padding: 12px; border-radius: 0 8px 8px 0; margin-top: 16px;
  white-space: pre-wrap; font-size: 0.95em; }}
@keyframes fadeSlide {{
  from {{ opacity: 0; transform: translateX(-20px); }}
  to {{ opacity: 1; transform: translateX(0); }}
}}
</style>
<h3>{title}</h3>
<ul>{points_html}</ul>
{f'<div class="example">{example}</div>' if example else ''}
</div>"""


def step_generate_questions(topic: str, subject: str, grade: int, course_title: str,
                            difficulty: str, num_questions: int, concept: dict) -> list[dict]:
    """Gemini generates quiz questions."""
    concept_ctx = json.dumps(concept, indent=2)
    prompt = f"""Generate exactly {num_questions} quiz questions on: "{topic}"
Course: {course_title} ({subject}), Grade {grade}
Difficulty: {difficulty}

Use this concept context for question design:
{concept_ctx}

Return JSON:
{{
  "questions": [
    {{
      "id": 1,
      "type": "multiple_choice",
      "question": "...",
      "options": ["A) ...", "B) ...", "C) ...", "D) ..."],
      "correct_answer": "A) ...",
      "explanation": "Why this is correct",
      "hint": "Optional hint"
    }}
  ]
}}

Rules:
- Mix types: ~60% multiple_choice, ~20% true_false, ~20% fill_blank
- For true_false: options should be ["True", "False"], correct_answer is "True" or "False"
- For fill_blank: options should be null, correct_answer is the text answer
- Questions progress from easy to hard
- All content in English
- Exactly {num_questions} questions"""
    try:
        result = _gemini_json(prompt)
        questions = result.get("questions", result if isinstance(result, list) else [])
        return questions
    except Exception as e:
        log.warning("generate_questions failed: %s", e)
        return []


def step_quality_check(questions: list[dict], num_questions: int) -> bool:
    """Validate question count, types, and answer completeness."""
    valid_types = {"multiple_choice", "true_false", "fill_blank"}
    return (
        len(questions) >= num_questions
        and all(q.get("correct_answer") for q in questions)
        and all(q.get("type") in valid_types for q in questions)
        and all(q.get("question") for q in questions)
    )


def step_build_html(
    concept: dict,
    questions: list[dict],
    video: dict,
    image_b64: str,
    animation_html: str,
    quiz_id: str,
    mode: str = "template",
    template: str = "random",
) -> str:
    """Combine animation (Phase 1) + quiz (Phase 2) into one HTML page."""
    from backend.app.quiz.templates import build_quiz_html

    supabase_url = settings.supabase_url
    supabase_key = settings.supabase_key

    # Build the quiz part
    if mode == "surprise":
        quiz_inner = _build_surprise_html(concept, questions, supabase_url, supabase_key)
        if not quiz_inner or "<script>" not in quiz_inner:
            log.warning("Surprise HTML invalid, falling back to template")
            quiz_inner = build_quiz_html(
                concept=concept,
                questions=questions,
                quiz_id=quiz_id,
                supabase_url=supabase_url,
                supabase_key=supabase_key,
                template="random",
            )
    else:
        quiz_inner = build_quiz_html(
            concept=concept,
            questions=questions,
            quiz_id=quiz_id,
            supabase_url=supabase_url,
            supabase_key=supabase_key,
            template=template,
        )

    # Build video + image HTML for learn phase
    video_html = ""
    if video.get("video_id"):
        from backend.app.services.youtube_search import build_youtube_embed
        video_html = build_youtube_embed(video["video_id"])

    image_html = ""
    if image_b64:
        from backend.app.services.image_gen import build_image_html
        image_html = build_image_html(image_b64, alt=concept.get("title", ""))

    if not animation_html and not video_html and not image_html:
        return quiz_inner

    return _build_two_phase_html(
        video_html=video_html,
        image_html=image_html,
        animation_html=animation_html,
        quiz_full_html=quiz_inner,
        video_title=video.get("title", ""),
        video_channel=video.get("channel", ""),
    )


def _build_two_phase_html(
    video_html: str,
    image_html: str,
    animation_html: str,
    quiz_full_html: str,
    video_title: str = "",
    video_channel: str = "",
) -> str:
    """Wrap learn resources + quiz into a single page with phase transition."""
    body_match = re.search(
        r"<body[^>]*>(.*)</body>", quiz_full_html, re.DOTALL | re.IGNORECASE
    )
    head_match = re.search(
        r"<head[^>]*>(.*)</head>", quiz_full_html, re.DOTALL | re.IGNORECASE
    )
    quiz_body = body_match.group(1) if body_match else quiz_full_html
    quiz_head = head_match.group(1) if head_match else ""

    video_section = ""
    if video_html:
        title_esc = video_title.replace('"', '&quot;').replace('<', '&lt;')
        channel_esc = video_channel.replace('"', '&quot;').replace('<', '&lt;')
        video_section = f"""
    <div class="learn-card">
      <div class="learn-label">📺 Watch</div>
      <div style="text-align:center">{video_html}</div>
      <div class="vid-meta">{title_esc}<br><span style="color:#999">{channel_esc}</span></div>
    </div>"""

    image_section = ""
    if image_html:
        image_section = f"""
    <div class="learn-card">
      <div class="learn-label">🖼️ Visualize</div>
      {image_html}
    </div>"""

    animation_section = ""
    if animation_html:
        animation_section = f"""
    <div class="learn-card">
      <div class="learn-label">🎬 Animation</div>
      {animation_html}
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{quiz_head}
<style>
* {{ box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; padding: 0; }}
#phase-learn {{ max-width: 680px; margin: 0 auto; padding: 16px; animation: fadeIn 0.4s ease; }}
#phase-quiz {{ display: none; animation: fadeIn 0.4s ease; }}
.learn-card {{
  background: #fff; border-radius: 16px; padding: 20px; margin-bottom: 16px;
  box-shadow: 0 2px 12px rgba(0,0,0,0.06);
}}
.learn-label {{
  font-size: 0.85em; font-weight: 700; text-transform: uppercase;
  letter-spacing: 1px; color: #6366f1; margin-bottom: 12px;
}}
.vid-meta {{
  text-align: center; font-size: 0.85em; color: #555; margin-top: 10px; line-height: 1.4;
}}
.phase-btn {{
  display: block; margin: 24px auto; padding: 16px 40px;
  background: linear-gradient(135deg, #6366f1, #8b5cf6); color: white;
  border: none; border-radius: 14px; font-size: 1.15em; font-weight: 700;
  cursor: pointer; box-shadow: 0 4px 20px rgba(99,102,241,0.35);
  transition: all 0.2s; letter-spacing: 0.5px;
}}
.phase-btn:hover {{ transform: translateY(-2px); box-shadow: 0 6px 28px rgba(99,102,241,0.45); }}
@keyframes fadeIn {{ from {{ opacity:0; transform:translateY(16px); }}
  to {{ opacity:1; transform:translateY(0); }} }}
</style>
</head>
<body>
<!-- Phase 1: Learn -->
<div id="phase-learn">
  {video_section}
  {image_section}
  {animation_section}
  <button class="phase-btn" onclick="startQuiz()">I'm ready for the quiz &rarr;</button>
</div>

<!-- Phase 2: Quiz -->
<div id="phase-quiz">
  {quiz_body}
</div>

<script>
function startQuiz() {{
  document.getElementById('phase-learn').style.display = 'none';
  document.getElementById('phase-quiz').style.display = 'block';
  window.scrollTo(0, 0);
}}
</script>
</body>
</html>"""


def _build_surprise_html(concept: dict, questions: list[dict],
                         supabase_url: str, supabase_key: str) -> str:
    """Let Gemini generate a complete HTML quiz page from scratch."""
    from backend.app.quiz.templates.base import SUPABASE_SUBMIT_JS

    submit_js = SUPABASE_SUBMIT_JS.replace(
        "{{SUPABASE_URL}}", supabase_url
    ).replace(
        "{{SUPABASE_KEY}}", supabase_key
    ).replace(
        "{{QUIZ_ID}}", "__QUIZ_ID__"
    )

    questions_json = json.dumps(questions, indent=2)
    concept_json = json.dumps(concept, indent=2)

    prompt = f"""Generate a COMPLETE, self-contained HTML page for an interactive quiz.

CREATIVE FREEDOM: Design a unique, visually stunning quiz experience.
Pick ONE creative theme. Examples:
- Space exploration with floating asteroids
- Underwater adventure
- Medieval quest / RPG
- Pixel art retro game
- Nature / garden growing

MUST include ALL of the following:
1. A concept explanation section (collapsible) using this data:
{concept_json}

2. Interactive questions using this data:
{questions_json}

3. Self-grading logic that compares user answers to correct_answer field
4. Animated result reveal showing score and per-question feedback
5. THIS EXACT JavaScript function (copy it exactly, do not modify):

{submit_js}

6. Call submitResults() after showing the results

Technical rules:
- Output ONLY the HTML. No markdown code fences.
- Single HTML file, zero external dependencies
- All CSS in <style> tags, all JS in <script> tags
- Responsive design (min-width: 320px)
- Smooth animations between question transitions"""
    try:
        html = _gemini_text(prompt, temperature=0.95)
        if html.startswith("```"):
            html = html.split("\n", 1)[1]
            html = html.rsplit("```", 1)[0]
        return html
    except Exception as e:
        log.warning("Surprise HTML generation failed: %s", e)
        return ""


# ── Main pipeline orchestrator ────────────────────────────


def run_quiz_pipeline(
    topic: str,
    subject: str,
    grade: int,
    course_title: str = "",
    difficulty: str = "medium",
    num_questions: int = 5,
    mode: str = "template",
    template: str = "random",
) -> dict[str, Any]:
    """Run the full quiz generation pipeline.

    Returns dict with keys: quiz_id, quiz_html, concept, questions, video, error
    """
    quiz_id = f"q_{uuid.uuid4().hex[:12]}"

    if not settings.gemini_api_key:
        return {"quiz_id": quiz_id, "quiz_html": "", "error": "GEMINI_API_KEY not configured"}

    try:
        # 1. Generate concept explanation
        log.info("Pipeline: generating concept for '%s'", topic)
        concept = step_generate_concept(topic, subject, grade, course_title, difficulty)

        # 2. Generate animation
        log.info("Pipeline: generating animation for '%s'", topic)
        animation_html = step_generate_animation(topic, subject, grade, concept)

        # 3. Generate questions (with retry)
        log.info("Pipeline: generating %d questions for '%s'", num_questions, topic)
        questions: list[dict] = []
        for attempt in range(MAX_RETRIES + 1):
            questions = step_generate_questions(
                topic, subject, grade, course_title, difficulty, num_questions, concept
            )
            if step_quality_check(questions, num_questions):
                break
            log.info("Quality check failed (attempt %d), retrying", attempt + 1)

        if not questions:
            return {"quiz_id": quiz_id, "quiz_html": "", "error": "Failed to generate questions"}

        # 4. Build HTML (no video/image — those have separate UI buttons)
        log.info("Pipeline: building HTML (mode=%s)", mode)
        quiz_html = step_build_html(
            concept=concept,
            questions=questions,
            video={},
            image_b64="",
            animation_html=animation_html,
            quiz_id=quiz_id,
            mode=mode,
            template=template,
        )

        return {
            "quiz_id": quiz_id,
            "quiz_html": quiz_html,
            "concept": concept,
            "questions": questions,
            "error": None,
        }

    except Exception as e:
        log.error("Quiz pipeline failed: %s", e)
        return {"quiz_id": quiz_id, "quiz_html": "", "error": str(e)}
