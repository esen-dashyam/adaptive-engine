"""Quiz template router — picks a template + color scheme and renders HTML."""
from __future__ import annotations

import random

from backend.app.quiz.templates.base import COLOR_SCHEMES, inject_config


def build_quiz_html(
    concept: dict,
    questions: list[dict],
    quiz_id: str,
    supabase_url: str,
    supabase_key: str,
    template: str = "random",
) -> str:
    """Build a self-contained quiz HTML page.

    template: "card_flip" | "progress_quest" | "classic_test" | "timed_challenge" | "random"
    """
    from backend.app.quiz.templates.card_flip import render as render_card_flip
    from backend.app.quiz.templates.classic_test import render as render_classic
    from backend.app.quiz.templates.progress_quest import render as render_progress
    from backend.app.quiz.templates.timed_challenge import render as render_timed

    renderers = {
        "card_flip": render_card_flip,
        "progress_quest": render_progress,
        "classic_test": render_classic,
        "timed_challenge": render_timed,
    }

    if template == "random" or template not in renderers:
        template = random.choice(list(renderers.keys()))

    scheme_name = random.choice(list(COLOR_SCHEMES.keys()))
    colors = COLOR_SCHEMES[scheme_name]

    html = renderers[template](concept, questions, colors)
    return inject_config(html, quiz_id, supabase_url, supabase_key)
