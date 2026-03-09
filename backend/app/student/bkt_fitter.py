"""
Bayesian Knowledge Tracing — Per-Skill EM Parameter Fitter.

Learns (p_init, p_transit, p_slip, p_guess) for each StandardsFrameworkItem
from the student response sequences stored in Neo4j (Student-ATTEMPTED->
GeneratedQuestion-TESTS->StandardsFrameworkItem).

Algorithm: Baum-Welch EM (HMM with monotone latent knowledge state).

Hidden state L_t:
  0 = not yet mastered
  1 = mastered

Transition (knowledge is monotone — once mastered, stays mastered):
  P(L_t=1 | L_{t-1}=0) = p_transit
  P(L_t=1 | L_{t-1}=1) = 1.0
  P(L_t=0 | L_{t-1}=1) = 0.0   (no forgetting)

Emission:
  P(correct | L_t=1) = 1 - p_slip
  P(correct | L_t=0) = p_guess

Initial:
  P(L_0=1) = p_init

The fitted params are written back to Neo4j on each StandardsFrameworkItem as:
  n.bkt_p_init, n.bkt_p_transit, n.bkt_p_slip, n.bkt_p_guess,
  n.bkt_fitted_at, n.bkt_n_sequences

Both BayesianSkillTracker and evaluation_agent read these node properties
(with hardcoded fallbacks) so every skill starts with sensible defaults before
enough data accumulates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

_EPS = 1e-12

# ── Parameter constraints ─────────────────────────────────────────────────────
# These bounds prevent degenerate solutions and keep BKT psychometrically valid.
_BOUNDS = {
    "p_init":    (0.01, 0.50),  # students rarely start fully mastered
    "p_transit": (0.01, 0.50),  # learning rate per attempt
    "p_slip":    (0.01, 0.35),  # slipping too often breaks the model
    "p_guess":   (0.10, 0.45),  # below 0.10 means always guessing wrong
}

# Default params used before any fitting (single source of truth for all BKT callers).
# Import these constants instead of hardcoding local values to keep the system
# consistent and to prevent silent parameter drift across modules.
DEFAULT_P_INIT     = 0.10   # prior probability of knowing a skill before any evidence
DEFAULT_P_TRANSIT  = 0.10   # per-attempt learning rate during formal assessments
DEFAULT_P_SLIP     = 0.08   # P(wrong | mastered) — slip rate
DEFAULT_P_GUESS    = 0.25   # P(correct | not mastered) — lucky-guess rate

# Exercises allow a faster learning rate than formal assessments because the
# student is in a low-stakes, targeted practice context.
EXERCISE_P_TRANSIT = 0.20


@dataclass
class FittedBKTParams:
    p_init:         float = DEFAULT_P_INIT
    p_transit:      float = DEFAULT_P_TRANSIT
    p_slip:         float = DEFAULT_P_SLIP
    p_guess:        float = DEFAULT_P_GUESS
    n_sequences:    int   = 0
    log_likelihood: float = float("-inf")


# ── Core HMM math ─────────────────────────────────────────────────────────────

def _emit(obs: bool, state: int, p_slip: float, p_guess: float) -> float:
    """P(observation | latent state)."""
    if state == 1:
        return (1.0 - p_slip) if obs else p_slip
    return p_guess if obs else (1.0 - p_guess)


def _forward(
    seq: list[bool],
    p_init: float,
    p_transit: float,
    p_slip: float,
    p_guess: float,
) -> tuple[list[list[float]], list[float]] | tuple[None, None]:
    """
    Scaled forward algorithm.

    Returns (alpha, scales) where:
      alpha[t][s] = P(L_t=s | o_1..o_t) (scaled so sum over s = 1)
      scales[t]   = un-normalised total at step t (product = likelihood)

    Returns (None, None) if any step produces a zero total.
    """
    n = len(seq)
    alpha: list[list[float]] = []
    scales: list[float] = []

    a0 = (1.0 - p_init) * _emit(seq[0], 0, p_slip, p_guess)
    a1 = p_init          * _emit(seq[0], 1, p_slip, p_guess)
    c  = a0 + a1
    if c < _EPS:
        return None, None
    scales.append(c)
    alpha.append([a0 / c, a1 / c])

    for t in range(1, n):
        prev = alpha[t - 1]
        # State 0: only reachable from state 0 (stays unmastered)
        new0 = prev[0] * (1.0 - p_transit) * _emit(seq[t], 0, p_slip, p_guess)
        # State 1: from state 0 via learning OR from state 1 (monotone)
        new1 = (prev[0] * p_transit + prev[1]) * _emit(seq[t], 1, p_slip, p_guess)
        c = new0 + new1
        if c < _EPS:
            return None, None
        scales.append(c)
        alpha.append([new0 / c, new1 / c])

    return alpha, scales


def _backward(
    seq: list[bool],
    p_transit: float,
    p_slip: float,
    p_guess: float,
    scales: list[float],
) -> list[list[float]]:
    """
    Scaled backward algorithm (divides by the same scale factors as forward).

    Returns beta[t][s] = P(o_{t+1}..o_T | L_t=s) (scaled).
    """
    n = len(seq)
    beta: list[list[float]] = [[1.0, 1.0]]  # beta_T = 1 for all states

    for t in range(n - 2, -1, -1):
        e0 = _emit(seq[t + 1], 0, p_slip, p_guess)
        e1 = _emit(seq[t + 1], 1, p_slip, p_guess)
        bt1 = beta[0]
        # b_t[0] = A[0->0]*e0*bt1[0] + A[0->1]*e1*bt1[1]
        b0 = (1.0 - p_transit) * e0 * bt1[0] + p_transit * e1 * bt1[1]
        # b_t[1] = A[1->0]*e0*bt1[0] + A[1->1]*e1*bt1[1] = 0 + e1*bt1[1]
        b1 = e1 * bt1[1]
        c = scales[t + 1]
        beta.insert(0, [b0 / c if c > _EPS else b0, b1 / c if c > _EPS else b1])

    return beta


def _e_step(
    alpha: list[list[float]],
    beta: list[list[float]],
    seq: list[bool],
    p_transit: float,
    p_slip: float,
    p_guess: float,
) -> tuple[list[list[float]], list[float], list[float]]:
    """
    Compute gamma (marginal state posteriors) and xi (transition posteriors).

    gamma[t][s] = P(L_t=s | all obs)
    xi_01[t]    = P(L_t=0, L_{t+1}=1 | all obs)   — the 0->1 transition
    xi_00[t]    = P(L_t=0, L_{t+1}=0 | all obs)   — the 0->0 transition
    """
    n = len(seq)
    gamma: list[list[float]] = []

    for t in range(n):
        g0 = alpha[t][0] * beta[t][0]
        g1 = alpha[t][1] * beta[t][1]
        norm = g0 + g1
        if norm < _EPS:
            gamma.append([0.5, 0.5])
        else:
            gamma.append([g0 / norm, g1 / norm])

    xi_01: list[float] = []
    xi_00: list[float] = []

    for t in range(n - 1):
        e0 = _emit(seq[t + 1], 0, p_slip, p_guess)
        e1 = _emit(seq[t + 1], 1, p_slip, p_guess)
        bt1 = beta[t + 1]

        x00 = alpha[t][0] * (1.0 - p_transit) * e0 * bt1[0]
        x01 = alpha[t][0] * p_transit           * e1 * bt1[1]
        x11 = alpha[t][1] * 1.0                 * e1 * bt1[1]
        total = x00 + x01 + x11

        if total < _EPS:
            xi_01.append(0.0)
            xi_00.append(gamma[t][0])
        else:
            xi_01.append(x01 / total)
            xi_00.append(x00 / total)

    return gamma, xi_01, xi_00


# ── Public fitting API ─────────────────────────────────────────────────────────

def fit_skill(
    sequences: list[list[bool]],
    n_iter: int = 50,
) -> FittedBKTParams:
    """
    Fit BKT parameters for one skill using Baum-Welch EM.

    Args:
        sequences: list of per-student response sequences
                   (True = correct, False = incorrect)
        n_iter:    maximum EM iterations

    Returns:
        FittedBKTParams with learned values clamped to valid ranges.
        Falls back to defaults if < 2 usable sequences.
    """
    seqs = [s for s in sequences if len(s) >= 2]
    if len(seqs) < 2:
        return FittedBKTParams(n_sequences=len(seqs))

    # Starting point
    p_init    = DEFAULT_P_INIT
    p_transit = DEFAULT_P_TRANSIT
    p_slip    = DEFAULT_P_SLIP
    p_guess   = DEFAULT_P_GUESS

    prev_ll = float("-inf")

    for iteration in range(n_iter):
        # ── E-step accumulators ───────────────────────────────────────────────
        init_num   = 0.0
        transit_num = 0.0;  transit_den = 0.0
        slip_num   = 0.0;   slip_den    = 0.0
        guess_num  = 0.0;   guess_den   = 0.0
        total_ll   = 0.0
        n_valid    = 0

        for seq in seqs:
            alpha, scales = _forward(seq, p_init, p_transit, p_slip, p_guess)
            if alpha is None:
                continue

            total_ll += sum(math.log(max(c, _EPS)) for c in scales)
            beta = _backward(seq, p_transit, p_slip, p_guess, scales)
            gamma, xi_01, xi_00 = _e_step(alpha, beta, seq, p_transit, p_slip, p_guess)

            # L0: expected probability of starting mastered
            init_num += gamma[0][1]
            n_valid  += 1

            for t, obs in enumerate(seq):
                g0, g1 = gamma[t]

                # Slip: wrong answer when actually mastered
                if not obs:
                    slip_num += g1
                slip_den += g1

                # Guess: correct answer when not mastered
                if obs:
                    guess_num += g0
                guess_den += g0

                # Transition 0->1 (learning events)
                if t < len(seq) - 1:
                    transit_num += xi_01[t]
                    transit_den += xi_01[t] + xi_00[t]  # P(L_t=0)

        if n_valid == 0:
            break

        # ── M-step: update parameters with bounds ─────────────────────────────
        def _clamp(val: float, key: str) -> float:
            lo, hi = _BOUNDS[key]
            return max(lo, min(hi, val))

        p_init    = _clamp(init_num   / n_valid,                  "p_init")
        p_transit = _clamp(transit_num / max(transit_den, _EPS),  "p_transit")
        p_slip    = _clamp(slip_num    / max(slip_den,    _EPS),   "p_slip")
        p_guess   = _clamp(guess_num   / max(guess_den,   _EPS),   "p_guess")

        if abs(total_ll - prev_ll) < 1e-4:
            logger.debug(f"BKT EM converged at iteration {iteration + 1}")
            break
        prev_ll = total_ll

    return FittedBKTParams(
        p_init=round(p_init, 4),
        p_transit=round(p_transit, 4),
        p_slip=round(p_slip, 4),
        p_guess=round(p_guess, 4),
        n_sequences=n_valid,
        log_likelihood=round(prev_ll, 3),
    )


# ── Neo4j integration ──────────────────────────────────────────────────────────

def calibrate_all_skills(
    driver,
    db_name: str = "neo4j",
    min_observations: int = 30,
) -> dict[str, Any]:
    """
    Run EM fitting for every StandardsFrameworkItem that has enough
    student response data.  Writes fitted params back to the node.

    Args:
        driver:           Neo4j driver instance
        db_name:          database name
        min_observations: minimum total ATTEMPTED records needed to fit

    Returns:
        Stats dict: {fitted, skipped, total_skills, errors}
    """
    stats = {"fitted": 0, "skipped": 0, "total_skills": 0, "errors": 0}

    with driver.session(database=db_name) as session:
        # Step 1: find skills with enough response data
        rows = session.run(
            """
            MATCH (q:GeneratedQuestion)-[:TESTS]->(n:StandardsFrameworkItem)
            MATCH (s:Student)-[:ATTEMPTED]->(q)
            RETURN n.identifier AS nid,
                   n.statementCode AS code,
                   count(*) AS total_attempts
            HAVING total_attempts >= $min_obs
            ORDER BY total_attempts DESC
            """,
            min_obs=min_observations,
        )
        skill_ids = [(r["nid"], r["code"], r["total_attempts"]) for r in rows]

    stats["total_skills"] = len(skill_ids)
    logger.info(f"BKT calibration: {len(skill_ids)} skills eligible (min_obs={min_observations})")

    for nid, code, _ in skill_ids:
        try:
            sequences = _fetch_sequences(driver, db_name, nid)
            if not sequences:
                stats["skipped"] += 1
                continue

            params = fit_skill(sequences)

            if params.n_sequences < 2:
                stats["skipped"] += 1
                continue

            _write_params(driver, db_name, nid, params)
            stats["fitted"] += 1
            logger.debug(
                f"Fitted {code}: L0={params.p_init} T={params.p_transit} "
                f"S={params.p_slip} G={params.p_guess} "
                f"(n={params.n_sequences} ll={params.log_likelihood})"
            )

        except Exception as exc:
            logger.warning(f"BKT calibration failed for {code}: {exc}")
            stats["errors"] += 1

    logger.info(
        f"BKT calibration done: fitted={stats['fitted']} "
        f"skipped={stats['skipped']} errors={stats['errors']}"
    )
    return stats


def _fetch_sequences(driver, db_name: str, node_id: str) -> list[list[bool]]:
    """
    Fetch per-student response sequences for one skill from Neo4j.

    Returns list of sequences (one per student), each ordered by timestamp.
    """
    with driver.session(database=db_name) as session:
        rows = session.run(
            """
            MATCH (q:GeneratedQuestion)-[:TESTS]->(n:StandardsFrameworkItem {identifier: $nid})
            MATCH (stu:Student)-[a:ATTEMPTED]->(q)
            RETURN stu.id AS student_id,
                   a.correct AS correct,
                   a.timestamp AS ts
            ORDER BY stu.id, a.timestamp
            """,
            nid=node_id,
        )
        data = [dict(r) for r in rows]

    if not data:
        return []

    # Group by student
    from collections import defaultdict
    by_student: dict[str, list[bool]] = defaultdict(list)
    for row in data:
        sid = row.get("student_id") or ""
        if sid:
            by_student[sid].append(bool(row.get("correct", False)))

    return list(by_student.values())


def _write_params(driver, db_name: str, node_id: str, params: FittedBKTParams) -> None:
    """Write fitted BKT params back to the StandardsFrameworkItem node."""
    with driver.session(database=db_name) as session:
        session.run(
            """
            MATCH (n:StandardsFrameworkItem {identifier: $nid})
            SET n.bkt_p_init     = $p_init,
                n.bkt_p_transit  = $p_transit,
                n.bkt_p_slip     = $p_slip,
                n.bkt_p_guess    = $p_guess,
                n.bkt_n_sequences = $n_seq,
                n.bkt_fitted_at  = datetime()
            """,
            nid=node_id,
            p_init=params.p_init,
            p_transit=params.p_transit,
            p_slip=params.p_slip,
            p_guess=params.p_guess,
            n_seq=params.n_sequences,
        )
