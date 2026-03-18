"""Shared JS/CSS snippets used by all quiz templates."""
from __future__ import annotations

import json

# ── Supabase submit function (injected into every quiz HTML) ─────────

SUPABASE_SUBMIT_JS = """
async function submitResults(quizId, score, total, answers) {
    const SUPABASE_URL = '{{SUPABASE_URL}}';
    const SUPABASE_KEY = '{{SUPABASE_KEY}}';
    const QUIZ_ID = quizId || '{{QUIZ_ID}}';
    try {
        const res = await fetch(
            SUPABASE_URL + '/rest/v1/quiz_sessions?id=eq.' + QUIZ_ID,
            {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    'apikey': SUPABASE_KEY,
                    'Authorization': 'Bearer ' + SUPABASE_KEY,
                    'Prefer': 'return=minimal'
                },
                body: JSON.stringify({
                    status: 'completed',
                    score: score,
                    total: total,
                    answers: answers,
                    completed_at: new Date().toISOString()
                })
            }
        );
        if (res.ok) {
            document.getElementById('save-status').textContent = 'Results saved!';
            document.getElementById('save-status').style.color = '#22c55e';
        } else {
            document.getElementById('save-status').textContent = 'Save failed — try refreshing.';
            document.getElementById('save-status').style.color = '#ef4444';
        }
    } catch (e) {
        document.getElementById('save-status').textContent = 'Offline — results shown locally.';
        document.getElementById('save-status').style.color = '#f59e0b';
    }
}
"""

# ── Color schemes ────────────────────────────────────────────────────

COLOR_SCHEMES = {
    "warm": {
        "bg": "linear-gradient(135deg, #fff5f5 0%, #fed7aa 100%)",
        "card": "#ffffff",
        "primary": "#f97316",
        "correct": "#22c55e",
        "wrong": "#ef4444",
        "text": "#1c1917",
        "muted": "#78716c",
        "accent": "#fb923c",
    },
    "cool": {
        "bg": "linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%)",
        "card": "#ffffff",
        "primary": "#3b82f6",
        "correct": "#22c55e",
        "wrong": "#ef4444",
        "text": "#1e293b",
        "muted": "#64748b",
        "accent": "#60a5fa",
    },
    "dark": {
        "bg": "linear-gradient(135deg, #1e1b4b 0%, #312e81 100%)",
        "card": "#1e293b",
        "primary": "#a78bfa",
        "correct": "#4ade80",
        "wrong": "#f87171",
        "text": "#f1f5f9",
        "muted": "#94a3b8",
        "accent": "#c4b5fd",
    },
    "forest": {
        "bg": "linear-gradient(135deg, #f0fdf4 0%, #dcfce7 100%)",
        "card": "#ffffff",
        "primary": "#16a34a",
        "correct": "#22c55e",
        "wrong": "#ef4444",
        "text": "#14532d",
        "muted": "#6b7280",
        "accent": "#4ade80",
    },
    "ocean": {
        "bg": "linear-gradient(135deg, #ecfeff 0%, #cffafe 100%)",
        "card": "#ffffff",
        "primary": "#0891b2",
        "correct": "#22c55e",
        "wrong": "#ef4444",
        "text": "#164e63",
        "muted": "#6b7280",
        "accent": "#22d3ee",
    },
    "sunset": {
        "bg": "linear-gradient(135deg, #fdf2f8 0%, #fce7f3 100%)",
        "card": "#ffffff",
        "primary": "#db2777",
        "correct": "#22c55e",
        "wrong": "#ef4444",
        "text": "#831843",
        "muted": "#9ca3af",
        "accent": "#f472b6",
    },
}


def inject_config(html: str, quiz_id: str, supabase_url: str, supabase_key: str) -> str:
    """Replace config placeholders in quiz HTML."""
    return (
        html.replace("{{SUPABASE_URL}}", supabase_url)
        .replace("{{SUPABASE_KEY}}", supabase_key)
        .replace("{{QUIZ_ID}}", quiz_id)
    )


def escape_json_for_html(data: dict | list) -> str:
    """Serialize data for embedding in a <script> tag."""
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
