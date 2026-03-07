# Adaptive Learning Engine

> An adaptive K1–K8 assessment platform combining a 144,733-node curriculum knowledge graph, Item Response Theory, Bayesian Knowledge Tracing, a Neuro-Symbolic Signal Bridge, and Google Gemini to deliver personalized assessments, detect learning gaps, and tutor students through a conversational AI interface.

---

## Table of Contents

- [What It Does](#what-it-does)
- [System Architecture](#system-architecture)
- [Core Features](#core-features)
- [Knowledge Graph](#knowledge-graph)
- [Learning Science](#learning-science)
- [API Reference](#api-reference)
- [Tech Stack](#tech-stack)
- [Quick Start](#quick-start)
- [Environment Variables](#environment-variables)
- [Project Structure](#project-structure)

---

## What It Does

```
Student answers questions
        │
        ▼
┌───────────────────┐     IRT Fisher Information ranking
│   PHASE A         │──►  GraphRAG context from Neo4j
│   Assessment      │     Gemini generates fresh questions
│   Generation      │     No static question bank
└───────────────────┘
        │
        ▼
┌───────────────────┐     Rasch 1PL θ update + SE computation
│   PHASE B         │──►  Elastic Stopping (SE < 0.30 or ≥ 25 Qs)
│   Deep            │     φ-Modified BKT mastery update
│   Evaluation      │     Misconception detection
│   (20-node        │     Cognitive Load Pruning (TEMPORARY_BLOCK)
│   LangGraph)      │     LCA Safety Net + Recursive Pivot
└───────────────────┘     Gap ranking + Remediation + Recommendations
        │
        ▼
┌───────────────────┐     Gemini-powered multi-turn tutor
│   AI TUTOR        │──►  Grounded in full evaluation result
│   Chat            │     Live exercise sessions with φ feedback
└───────────────────┘     Back-and-Forth recursive adaptation
```

---

## System Architecture

### Phase A — Assessment Generation

1. **IRT Standard Selection** — Queries Neo4j for curriculum standards ranked by Fisher Information `I(θ,β) = P(1−P)`, maximizing diagnostic signal at the student's current ability level θ. Excludes already-asked nodes and concepts currently TEMPORARY_BLOCKed for the student.
2. **GraphRAG Context** — Fetches prerequisite chains, sibling standards, and mastery context from Neo4j to ground question generation.
3. **Gemini Question Generation** — Generates fresh multiple-choice questions calibrated to grade level, DOK target, and student θ. Zero static question bank.

### Phase B — Deep Evaluation (20-node LangGraph pipeline)

```
detect_confusion_signal
  ├─ confused ──► lca_confusion ──► write_report (scaffold + anchor)
  └─ normal
       │
       ▼
  score_answers ──► chat_to_signal (φ auditor) ──► update_rasch
       │
       ▼
  check_stopping_criterion
  ├─ SE ≥ 0.30 AND total < 25 ──► generate_follow_up_questions ──► write_report
  └─ continue
       │
       ▼
  detect_misconceptions ──► lca_misconception ──► update_bkt
       │
       ▼
  consolidate_memory ──► load_exercise_memory ──► identify_and_rank_gaps
       │
       ├─ gaps ──► generate_remediation ──► judge_mastery
       └─ no gaps ──────────────────────►  judge_mastery
                                               │
                                               ▼
                                     apply_fidelity_correction
                                               │
                                               ▼
                                     generate_recommendations
                                               │
                                               ▼
                                     llm_recommendation_decider ──► write_report
```

---

## Core Features

### Elastic Stopping (Computerized Adaptive Testing)

The pipeline stops asking questions when the Rasch Standard Error drops below the confidence threshold, not after a fixed count.

```
Stop when:  SE(θ) < 0.30   OR   total_answered ≥ 25

SE(θ) = 1 / √(Σ I(θ, βᵢ))    where I(θ,β) = P(1−P)
```

If SE ≥ 0.30 after initial questions, the pipeline routes to `generate_follow_up_questions` and returns `needs_more_questions: true` with a fresh question batch. The frontend loops back with `total_answered_prior` to accumulate the count.

---

### Neuro-Symbolic Signal Bridge (φ System)

The central innovation: every student action produces a **Fidelity Factor φ ∈ [−1.0, 1.0]** that the Gemini Dynamic Weight Auditor computes from chat messages, response time, correctness, and DOK level.

| φ value | Signal | Meaning |
|---------|--------|---------|
| `+1.0` | Fluent | Explained reasoning correctly — genuine understanding |
| `+0.7` | Confident | Correct, moderate speed, no hesitation |
| `+0.5` | Partial | Hesitant ("I think…") or suspiciously fast |
| `+0.2` | Brittle | Correct in < 3 s on DOK ≥ 2 — likely pattern match |
| `0.0` | Neutral | Wrong answer — BKT posterior already penalizes |
| `−0.5` | Struggling | Partial conceptual gap, specific hurdle identified |
| `−1.0` | Hard Block | "I don't get this" — prerequisite fundamentally missing |

**φ-Modified BKT Formula:**

```
P(L_{t+1}) = P(L_t | Obs) + (1 − P(L_t | Obs)) × (p_transit × φ)
```

Negative φ produces un-learning. The standard BKT formula (φ=1) is a special case. Wrong answers cap φ at 0.0 to avoid double-penalizing.

---

### Live Confusion Signal Backprop

If a student sends "I don't get this / why / how" during a session, the signal is captured as `confusion_signal: true` before Phase B scoring begins. The pipeline immediately routes to `lca_confusion` (BFS backward to nearest mastered ancestor) and exits with a scaffold response — skipping the full 20-node evaluation. No wasted scoring on a student who has already disengaged.

---

### Cognitive Load Pruning (TEMPORARY_BLOCK)

When a concept is hard-blocked (φ = −1.0 or prerequisite failed), the system writes `TEMPORARY_BLOCK` relationships in Neo4j for all downstream concepts (1–4 hops forward) to prevent overwhelming the student:

```cypher
(:Student)-[:TEMPORARY_BLOCK {
  blocked_by: $hard_blocked_node_id,
  created_at: datetime()
}]->(:SFI)
```

These blocks are automatically removed when the student achieves mastery ≥ 0.65 on the blocking concept during a future exercise session.

---

### Recursive Pivot — Back-and-Forth Adaptation

During live exercise sessions (`/exercise_chat`), φ is computed in real time. When φ < −0.3:

1. **The Back** — LCA agent BFS-traverses the prerequisite graph to find the nearest mastered ancestor (p_mastery ≥ 0.95, up to 6 hops).
2. **The Bridge** — Gemini generates a 2–3 sentence connecting instruction: "You already know X — here's how that connects to Y."
3. **The Forth** — Student resumes the original exercise with the bridge as scaffolding.

Every φ < −0.3 event is logged as an immutable **FailureChain** audit record in PostgreSQL.

---

### Fidelity Correction Layer

After `judge_mastery`, the LLM verdict and response-time signals are used to apply a correction to the BKT gain — not the total mastery:

```python
corrected = p_mastery_before + gain * fidelity_factor
```

| Condition | Factor |
|-----------|--------|
| Mastered verdict + challenge question | 1.2× |
| Struggling verdict + correct answer | 0.5× |
| `is_likely_guess` flag set | 0.5× |
| Default | 1.0× |

This prevents a lucky guess from inflating mastery or a slow-but-correct answer from being penalized.

---

### Per-Standard BKT Parameter Fitting

BKT parameters (p_init, p_transit, p_slip, p_guess) are fit per standard using Baum-Welch EM on historical student response data. Standards with insufficient data fall back to grade-band defaults. Fit parameters are stored in Neo4j and refreshed as more data accumulates.

---

### Exercise Memory & Anti-Repetition

Before generating remediation exercises, the system loads the student's full exercise history per standard from Neo4j (`ATTEMPTED` edges). The remediation prompt receives a memory block showing all previously seen questions and explicitly instructs Gemini to approach the concept from a different angle. Students who have struggled across multiple sessions (< 40% correct) receive more concrete, real-world framings.

---

### AI Tutor

After assessment results load, a Gemini-powered tutor opens automatically. The tutor is grounded in the complete evaluation payload: θ, every gap with mastery probability, misconceptions, remediation exercises, and recommended learning path. Students can ask follow-up questions; the tutor has access to the student's full Neo4j mastery profile for standalone chat sessions.

---

## Knowledge Graph

Neo4j holds **144,733 StandardsFrameworkItem** nodes covering K–12 Mathematics and English Language Arts across all 50 US states plus CCSS Multi-State standards.

### Node & Edge Schema

| Edge type | Meaning | Weight |
|-----------|---------|--------|
| `BUILDS_TOWARDS` | Conceptual prerequisite | `conceptual_weight` (0–1) |
| `PRECEDES` | Ordered sequence | — |
| `DEFINES_UNDERSTANDING` | Understanding dependency | `understanding_strength` |
| `HAS_CHILD` | Curriculum cluster | — |

### Student State Edges

```cypher
(:Student)-[:SKILL_STATE {
  p_mastery,      // Bayesian posterior P(mastered)
  p_transit,      // P(learning on next attempt) — fit per standard
  p_slip,         // P(knows but answers wrong)
  p_guess,        // P(doesn't know but guesses right)
  attempts,
  correct,
  last_updated
}]->(:StandardsFrameworkItem)

(:Student)-[:TEMPORARY_BLOCK {
  blocked_by,     // identifier of the blocking hard concept
  created_at
}]->(:StandardsFrameworkItem)

(:Student)-[:ATTEMPTED {
  question_text,
  correct,
  dok_level,
  session_id,
  timestamp
}]->(:StandardsFrameworkItem)
```

### Edge Weight Learning

`BUILDS_TOWARDS` edge weights are updated after every session using exponential moving average:

```
new_weight = old_weight × 0.95 + signal × 0.05
```

where `signal = 1.0` if the downstream concept was mastered after mastering the prerequisite, `0.0` otherwise.

---

## Learning Science

### Rasch 1PL Item Response Theory

```
P(correct | θ, β) = 1 / (1 + exp(−(θ − β)))

I(θ, β) = P × (1 − P)          // Fisher Information
SE(θ)   = 1 / √(Σ I(θ, βᵢ))   // Standard Error of θ estimate
```

θ is updated via Newton-Raphson after each response. Questions are selected to maximize `I(θ,β)` — the engine targets β ≈ θ (50% success probability) for maximum diagnostic signal.

### φ-Modified Bayesian Knowledge Tracing

```
// Bayesian update on observation:
P(L | correct) = P(L) × (1 − p_slip) / [P(L)(1−p_slip) + (1−P(L)) × p_guess]
P(L | wrong)   = P(L) × p_slip       / [P(L) × p_slip + (1−P(L)) × (1−p_guess)]

// φ-modified learning transition:
P(L_{t+1}) = P(L_t | Obs) + (1 − P(L_t | Obs)) × (p_transit × φ)

// φ < 0 → un-learning (mastery decreases)
// φ = 1 → standard BKT
// φ = 0 → no update (wrong answer, BKT posterior already penalized)
```

### Knowledge Space Theory (KST) Propagation

After scoring, mastery propagates through the prerequisite graph:

- **Forward (success):** child mastery propagates upward → parent mastery × 0.90 per hop
- **Backward (failure):** parent failure propagates downward → child mastery × 0.70 penalty per hop
- **Hard block:** prerequisite edge with `conceptual_weight ≥ 0.9` failed → all downstream nodes marked hard-blocked → `TEMPORARY_BLOCK` written to Neo4j

---

## API Reference

### Assessment

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/assessment/generate` | Phase A: IRT selection + GraphRAG + question generation |
| `POST` | `/api/v1/assessment/evaluate` | Phase B: full 20-node adaptive evaluation pipeline |
| `POST` | `/api/v1/assessment/exercise_chat` | Live exercise session: φ computation + recursive pivot |
| `POST` | `/api/v1/assessment/exercise_complete` | Mark exercise done, update Neo4j, unblock if mastered |

### AI Tutor

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/chat/tutor` | Multi-turn chat grounded in a full EvalResult payload |
| `POST` | `/chat/standalone` | Multi-turn chat grounded in live Neo4j mastery profile |
| `GET`  | `/chat/context/{student_id}` | Load student mastery profile for standalone tutor |

### `/evaluate` Response Shape

```json
{
  "student_id": "student_001",
  "score": 0.75,
  "theta": 0.42,
  "se": 0.28,
  "total_answered": 10,
  "needs_more_questions": false,
  "additional_questions": [],
  "gaps": [...],
  "misconceptions": [...],
  "remediation_plan": [...],
  "recommendations": [...],
  "lca_safety_nets": {},
  "newly_blocked_nodes": [],
  "mastery_verdicts": {},
  "llm_decisions": {}
}
```

### `/exercise_chat` Response Shape

```json
{
  "phi": -0.5,
  "reason": "Student expressed confusion about regrouping",
  "gap_tag": "regrouping",
  "p_mastery_before": 0.72,
  "p_mastery_after": 0.31,
  "pivot_needed": true,
  "pivot_node": { "code": "2.NBT.A.1", "description": "...", "hops": 2 },
  "bridge_instruction": "You already know how to count tens — let's use that..."
}
```

### Example: Generate an Assessment

```bash
curl -X POST http://localhost:8000/api/v1/assessment/generate \
  -H "Content-Type: application/json" \
  -d '{
    "student_id": "student_001",
    "grade": "3",
    "subject": "math",
    "state_jurisdiction": "TX"
  }'
```

### Example: Submit Answers

```bash
curl -X POST http://localhost:8000/api/v1/assessment/evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "student_id": "student_001",
    "grade": "3",
    "subject": "math",
    "state_jurisdiction": "TX",
    "answers": {"<question_id>": "A"},
    "questions": [...],
    "confusion_signal": false,
    "total_answered_prior": 0
  }'
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11, FastAPI, LangGraph |
| AI | Google Gemini (gemini-2.5-pro / gemini-2.0-flash) via Vertex AI |
| Knowledge graph | Neo4j 5 — 144,733 curriculum nodes |
| Relational DB | PostgreSQL 15 — sessions, failure chains, audit log |
| Frontend | Next.js 14, TypeScript, TailwindCSS |
| Infrastructure | Docker Compose |

---

## Quick Start

### Prerequisites

- Docker Desktop
- Python 3.11 + Poetry
- Node.js 18+
- Gemini API key (free at aistudio.google.com) or GCP project with Vertex AI enabled

### 1. Clone and configure

```bash
git clone <repo>
cd adaptive-learning-engine
cp .env.example .env
# Fill in: GEMINI_API_KEY, NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, POSTGRES_URL
```

### 2. Start databases

```bash
docker compose -f infra/compose.yaml up -d
```

### 3. Start backend

```bash
poetry install
poetry run uvicorn backend.app.main:app --reload --port 8000
```

### 4. Start frontend

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000)

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `NEO4J_URI` | Neo4j Bolt URI (e.g. `bolt://localhost:7687`) |
| `NEO4J_USER` | Neo4j username |
| `NEO4J_PASSWORD` | Neo4j password |
| `POSTGRES_URL` | PostgreSQL connection string |
| `GEMINI_API_KEY` | Gemini API key |
| `GCP_PROJECT_ID` | GCP project for Vertex AI |
| `GCP_LOCATION` | GCP region (default: `us-central1`) |
| `AGENT_MAX_QUESTIONS` | Cap on questions per assessment (default: 15) |

---

## Project Structure

```
adaptive-learning-engine/
├── backend/
│   └── app/
│       ├── agents/
│       │   ├── orchestrator.py          # LangGraph pipeline — Phase A + Phase B wiring
│       │   ├── assessment_agent.py      # Phase A: IRT selection + GraphRAG + generation
│       │   ├── evaluation_agent.py      # Phase B: scoring, Rasch, φ-BKT, misconceptions
│       │   ├── adaptive_agents.py       # Elastic stopping, confusion signal, LCA, follow-up Qs
│       │   ├── signal_bridge.py         # Neuro-symbolic φ system + recursive pivot
│       │   ├── gap_agent.py             # KST propagation, gap ranking, TEMPORARY_BLOCK
│       │   ├── remediation_agent.py     # NanoPoint-tagged exercise generation
│       │   ├── recommendation_agent.py  # ZPD frontier + learning path
│       │   ├── metacognitive_agent.py   # LLM mastery judge + fidelity correction
│       │   ├── memory_agent.py          # Session memory consolidation
│       │   ├── lca_agent.py             # BFS backward to nearest mastered ancestor
│       │   └── vertex_llm.py            # Gemini client (GenAI + Vertex AI fallback)
│       ├── api/routes/
│       │   ├── assessment.py            # /generate, /evaluate, /exercise_chat, /exercise_complete
│       │   └── chat.py                  # AI tutor endpoints
│       ├── agent/state.py               # LangGraph AssessmentState — all 30+ fields
│       ├── student/
│       │   ├── rasch_engine.py          # Rasch 1PL + Newton-Raphson θ update
│       │   ├── bayesian_tracker.py      # φ-modified BKT per-standard update
│       │   └── bkt_fitter.py            # Baum-Welch EM parameter fitting
│       ├── db/models/
│       │   ├── chat.py                  # FailureChain + ChatSession ORM models
│       │   └── __init__.py
│       └── core/settings.py             # Pydantic settings
├── frontend/
│   └── app/
│       └── assessment/page.tsx          # Assessment UI + tutor chat + parent dashboard
├── infra/
│   └── compose.yaml                     # Neo4j + PostgreSQL
└── ARCHITECTURE.md                      # Full system design reference
```

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the complete system design including all Neo4j schemas, PostgreSQL tables, the full 20-node Phase B pipeline, and the φ signal reference table.
