"""
Rasch 1PL Item Response Theory (IRT) — Estimation Algorithm.

The "Brain" of the adaptive test.

Instead of a raw percentage score, this module computes a Logit Ability (θ)
for the student.  Every concept node in the KG has a Difficulty (β).
After every answer, θ is updated via a Newton-Raphson step.

Key formulas:
  P(correct | θ, β) = 1 / (1 + exp(-(θ - β)))   — Rasch model
  Fisher Information I(θ) = P * (1 - P)           — maximised when θ ≈ β
  θ update (MLE gradient step): θ += (correct - P) / I(θ)

Grade → difficulty mapping (logit scale):
  Grade K  → -3.0   (very easy)
  Grade 1  → -2.0
  Grade 2  → -1.5
  Grade 3  → -1.0
  Grade 4  → -0.5
  Grade 5  →  0.0   (average adult)
  Grade 6  →  0.5
  Grade 7  →  1.0
  Grade 8  →  1.5
  Grade 9+ →  2.0
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


# ── Constants ─────────────────────────────────────────────────────────────────

GRADE_DIFFICULTY: dict[str, float] = {
    "k":  -3.0, "1": -2.0, "2": -1.5, "3": -1.0, "4": -0.5,
    "5":   0.0, "6":  0.5, "7":  1.0, "8":  1.5, "9":  2.0,
}

DOK_OFFSET: dict[int, float] = {1: -0.5, 2: 0.0, 3: 0.5, 4: 1.0}

THETA_MIN = -4.0
THETA_MAX =  4.0
STEP_SIZE = 0.5   # learning rate for Newton-Raphson step


# ── Core math ─────────────────────────────────────────────────────────────────

def p_correct(theta: float, beta: float) -> float:
    """Probability of correct response given ability θ and difficulty β."""
    try:
        return 1.0 / (1.0 + math.exp(-(theta - beta)))
    except OverflowError:
        return 0.0 if (theta - beta) < 0 else 1.0


def fisher_information(theta: float, beta: float) -> float:
    """Fisher Information at (θ, β).  Maximum when θ == β (value = 0.25)."""
    p = p_correct(theta, beta)
    return p * (1.0 - p)


def update_theta(theta: float, beta: float, correct: bool) -> float:
    """
    One Newton-Raphson MLE step after a single response.

    Δθ = (observed - expected) / Fisher_Information
       clamped to [THETA_MIN, THETA_MAX]
    """
    p = p_correct(theta, beta)
    info = fisher_information(theta, beta)
    observed = 1.0 if correct else 0.0
    if info < 1e-6:
        return theta
    delta = STEP_SIZE * (observed - p) / info
    return max(THETA_MIN, min(THETA_MAX, theta + delta))


# ── Grade → logit helpers ─────────────────────────────────────────────────────

def grade_to_difficulty(grade: str, dok_level: int = 2, category: str = "target") -> float:
    """
    Convert a grade string + DOK level to a Rasch difficulty logit (β).

    'prerequisite' questions are one grade easier than target questions.
    """
    key = grade.lower().replace("k", "k").strip()
    if key.startswith("k"):
        key = "k"
    base = GRADE_DIFFICULTY.get(key, 0.0)
    if category == "prerequisite":
        base -= 0.5
    base += DOK_OFFSET.get(dok_level, 0.0)
    return round(base, 2)


# ── Session-level estimator ────────────────────────────────────────────────────

@dataclass
class RaschSession:
    """
    Tracks one student's θ across the full assessment.

    Usage:
        session = RaschSession(initial_theta=0.0)
        for question, answered_correctly in responses:
            beta = grade_to_difficulty(question["grade"], question["dok_level"])
            session.record(question["id"], beta, answered_correctly)
        print(session.theta, session.se)
    """
    initial_theta: float = 0.0
    theta: float = field(init=False)
    history: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self.theta = self.initial_theta

    def record(self, question_id: str, beta: float, correct: bool) -> float:
        """Update θ after one answer.  Returns new θ."""
        old_theta = self.theta
        self.theta = update_theta(self.theta, beta, correct)
        self.history.append({
            "question_id": question_id,
            "beta": beta,
            "correct": correct,
            "theta_before": old_theta,
            "theta_after": self.theta,
            "p_correct": p_correct(old_theta, beta),
        })
        return self.theta

    @property
    def se(self) -> float:
        """Standard Error of θ estimate (1 / sqrt(sum of information))."""
        total_info = sum(
            fisher_information(h["theta_before"], h["beta"])
            for h in self.history
        )
        return 1.0 / math.sqrt(total_info) if total_info > 0 else 9.99

    @property
    def grade_equivalent(self) -> str:
        """Map current θ to a grade-equivalent label."""
        inv = {v: k for k, v in GRADE_DIFFICULTY.items()}
        closest = min(inv.keys(), key=lambda b: abs(self.theta - b))
        return f"Grade {inv[closest].upper()}"

    def to_dict(self) -> dict:
        return {
            "theta": round(self.theta, 3),
            "se": round(self.se, 3),
            "grade_equivalent": self.grade_equivalent,
            "n_items": len(self.history),
            "history": self.history,
        }
