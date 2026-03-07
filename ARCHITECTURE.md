# Adaptive Learning Engine — Architecture

## The Big Picture

An adaptive K1–K8 educational assessment engine with an integrated AI Tutor. Instead of handing every student the same test, the system selects questions personalized to each student's current ability using Item Response Theory, evaluates answers with real learning-science algorithms, generates targeted remediation exercises, and then lets the student chat with an AI tutor that is fully grounded in their actual results.

```
Student (browser)
    │
    ▼
Next.js Frontend (localhost:3000)
    │ POST /api/v1/assessment/generate      — Phase A: adaptive question set
    │ POST /api/v1/assessment/evaluate      — Phase B: full evaluation pipeline
    │ POST /api/v1/chat/tutor               — AI Tutor (post-assessment)
    │ POST /api/v1/chat/standalone          — AI Tutor (any time, from live mastery)
    │ GET  /api/v1/chat/context/{id}        — Load student mastery context
    ▼
FastAPI Backend (localhost:8000)
    │
    ├─ Phase A LangGraph ──► Neo4j KG + Gemini Flash
    ├─ Phase B LangGraph ──► Neo4j KG + Gemini Flash + Postgres
    └─ AI Tutor ───────────► Neo4j KG + Gemini 2.5 Pro
```

---

## Infrastructure

| Layer | Technology | What it stores |
|---|---|---|
| **Graph DB** | Neo4j (Docker `ale-neo4j`) | 144K+ `StandardsFrameworkItem` nodes (CCSS, TEKS, etc.) with `BUILDS_TOWARDS`, `PRECEDES`, `HAS_CHILD` edges; `SKILL_STATE` edges per student |
| **Relational DB** | Postgres (Docker `ale-postgres`) | `AssessmentSession` rows, student records |
| **LLM** | Gemini 2.0 Flash (question gen) + Gemini 2.5 Pro (AI Tutor) via Vertex AI REST | Question generation, misconception diagnosis, remediation, recommendations, tutoring |
| **Backend** | FastAPI + LangGraph + Poetry venv | Orchestrates all agents |
| **Frontend** | Next.js + TailwindCSS | Assessment UI, results display, AI Tutor chat |

---

## The Two-Layer Agent System

There are **two separate agent implementations** that coexist in the repo — a legacy one and the active new one.

### Old system: `backend/app/agent/` (legacy, dormant)

```
backend/app/agent/
  state.py       ← shared AssessmentState schema, still imported by new system
  graph.py       ← OLD two-phase LangGraph (simple flat nodes)
  nodes.py       ← OLD node functions (select_standards, evaluate_answers…)
```

This was the original flat agent. **The API no longer calls this code.** `state.py` is still imported by the new agents — it holds the shared `AssessmentState` Pydantic model.

### New system: `backend/app/agents/` (active — what the API calls)

```
backend/app/agents/
  orchestrator.py          ← NEW LangGraph, imports from all agents below
  assessment_agent.py      ← Phase A: IRT-ranked standards + RAG + Gemini questions
  evaluation_agent.py      ← Phase B steps 1–4: score + Rasch + misconceptions + BKT
  gap_agent.py             ← Phase B step 5: KST gap analysis + ranking
  remediation_agent.py     ← Phase B step 6: Gemini targeted exercises
  recommendation_agent.py  ← Phase B step 7: ZPD frontier + Gemini learning path
  rasch.py                 ← Rasch 1PL IRT math library
  kst.py                   ← Knowledge Space Theory propagation
  irt_selector.py          ← Maximum Information Gain selector
  vertex_llm.py            ← Unified Gemini/Vertex LLM client (generate + chat)
```

The FastAPI routes in `api/routes/assessment.py` import from `orchestrator.py`.
The AI Tutor lives entirely in `api/routes/chat.py`.

---

## Phase A — Question Generation

```
API receives: {grade, subject, student_id, state, num_questions}
                    │
                    ▼
        1. _load_student_theta
           MATCH (s:Student {id})-[r:SKILL_STATE]->()
           mean_p = avg(r.p_mastery) → θ = logit(mean_p)
           New students → θ = 0.0
                    │
                    ▼
        assessment_agent: select_standards_irt
           ┌─────────────────────────────────────┐
           │ Cypher → StandardsFrameworkItem       │
           │   WHERE gradeLevelList = grade        │
           │     AND academicSubject = subject     │
           │     AND jurisdiction = state          │
           │   Fallback to Multi-State if < 3 hits │
           │ + prerequisite grade (grade-1) nodes  │
           │ + BUILDS_TOWARDS / HAS_DEPENDENCY /   │
           │   DEFINES_UNDERSTANDING edges for IRT │
           └─────────────────────────────────────┘
                    │
                    ▼
        irt_selector: rank_nodes_by_information
           β = grade_to_difficulty(grade, dok_level, category)
           info = P*(1-P) where P = sigmoid(θ - β)
           Multi-domain nodes get 1.5× intersection bonus
           Target: 7-12 target nodes, 3-5 prereq nodes
           Cap at settings.agent_max_questions (10)
           Prereq/target ratio preserved when capping
                    │
                    ▼
        assessment_agent: fetch_rag_context
           Per node: prereqs (BUILDS_TOWARDS → n),
           sibling standards (same grade+subject),
           existing question stems (GeneratedQuestion → TESTS → n)
           Builds rag_prompt_block text for Gemini
                    │
                    ▼
        assessment_agent: generate_questions
           Gemini Flash call → JSON array of N questions
           A/B/C/D options, correct answer, dok_level,
           standard_code, node_index for node_ref linkage
           generate_json() unwraps dict wrappers + retries once
```

**Output to frontend:** `assessment_id`, `questions[]`, `theta`, `question_difficulties`, `core_standards`.

---

## Phase B — Evaluation Pipeline

```
API receives: {assessment_id, student_id, answers[]}
                    │
                    ▼
   evaluation_agent: score_answers
      q_map = {question_id: question} from state.questions
      Compare selected_answer vs q["answer"] → is_correct
      score = correct_count / total
                    │
                    ▼
   evaluation_agent: update_rasch
      RaschSession(initial_theta=θ)
      For each answer in submission order:
        θ = update_theta(θ, β, is_correct)
        Δθ = 0.5 * (observed - P(θ,β)) / I(θ,β)
        θ clamped to [-4.0, +4.0]
      Produces theta, theta_history[], SE, grade_equivalent
                    │
                    ▼
   evaluation_agent: detect_misconceptions
      Wrong answers (up to 6) → Gemini Flash prompt:
      "What misconception led to this error?"
      Returns: [{question_id, standard_code, misconception,
                 affected_standards[], mastery_penalty}]
      Penalties accumulated per standard (max 0.5 total per node)
                    │
                    ▼
   evaluation_agent: update_bkt
      For each tested node:
        p_before = Neo4j SKILL_STATE r.p_mastery (or P_INIT=0.1)
        p_before_adj = max(0.05, p_before - misconception_penalty)
        p_after = BKT_update(p_before_adj, is_correct)
        MERGE SKILL_STATE with p_mastery, attempts, correct, last_updated
                    │
                    ▼
   gap_agent: identify_and_rank_gaps
      Fetch PRECEDES / BUILDS_TOWARDS / HAS_CHILD edges (2-hop)
      Downstream impact count per tested node
      Run KST propagation (see below)
      Gaps = nodes where KST mastery < 0.55
      Rank: hard-blocked first → downstream_count desc → mastery asc
      Cap at 8 gaps reported
                    │
          ┌─────────┴──────────┐
        gaps?                no gaps
          │                    │
          ▼                    ▼
   remediation_agent      skip remediation
      Per gap (up to 5):
        is_hard or mastery < 0.25 → "foundational re-teaching" (DOK 1)
        mastery < 0.45           → "guided practice" (DOK 2)
        else                     → "application & problem-solving" (DOK 2)
        Gemini generates 3 exercises with hint + answer + explanation
        Exercises informed by misconception context
      Ordered: hard-blocked first, then by mastery ascending
          │
          ▼
   recommendation_agent
      identify_frontier(knowledge_state, edges, threshold=0.60)
        frontier = unmastered nodes whose all prereqs ARE mastered
        Fallback: lowest-mastery unblocked nodes
      Score frontier by Fisher Information I(θ,β) at student θ
      Top 5 frontier nodes → Gemini:
        why_now, how_to_start, estimated_minutes, difficulty label
      Output: rank, standard_code, description, success_prob,
              current_mastery, information_score
                    │
                    ▼
   write_report → return full result to frontend
```

---

## AI Tutor System (`api/routes/chat.py`)

The AI Tutor is a fully separate feature from the assessment pipeline. It uses **Gemini 2.5 Pro** for stronger pedagogical reasoning. There are two modes:

### Mode 1 — Post-Assessment Tutor (`POST /api/v1/chat/tutor`)

Used immediately after an assessment. The frontend sends the complete `EvalResult` payload as `context`. The tutor knows:
- Exact score, θ, grade status, correct/incorrect counts
- Every gap with its mastery %, priority, and hard-block status
- Every LLM-detected misconception with standard code
- Every ZPD recommendation with why_now, how_to_start, estimated_minutes
- Every wrong answer (question text, student answer, correct answer)

The system prompt (`_build_system_prompt`) builds a structured document with sections:
```
=== STUDENT PROFILE ===        — grade, score, θ, status
=== KNOWLEDGE GAPS (N) ===     — per gap: code, desc, mastery%, priority, hard-blocked flag
=== DETECTED MISCONCEPTIONS === — per misconception: code + root cause
=== NEXT LEARNING STEPS ===    — ZPD frontier items with why/how/time
=== QUESTIONS ANSWERED INCORRECTLY === — up to 6 wrong Q+A pairs
=== TUTORING GUIDELINES ===    — 10 behavioural rules for the tutor
```

Request shape:
```json
{
  "student_id": "...",
  "grade": "K5",
  "subject": "math",
  "message": "Can you explain what I got wrong on fractions?",
  "history": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}],
  "context": { /* full EvalResult payload */ }
}
```

### Mode 2 — Standalone Tutor (`POST /api/v1/chat/standalone`)

Works any time, not just after an assessment. Fetches live mastery data from Neo4j via `GET /chat/context/{student_id}` and builds a different system prompt (`_build_system_from_mastery`):

```
Standards assessed: N/total  |  Mean mastery: X%
=== KNOWLEDGE GAPS (N standards below 55% mastery) ===
=== STRENGTHS (above 70% mastery) ===
=== MASTERY BY GRADE ===     — per-grade count + avg mastery
=== RECENTLY ASSESSED STANDARDS ===
=== TUTORING GUIDELINES ===
```

### `GET /api/v1/chat/context/{student_id}` — Mastery Context Loader

Queries Neo4j `SKILL_STATE` edges for a student and returns a structured profile:
```json
{
  "student_id": "...",
  "has_history": true,
  "total_assessed": 47,
  "total_in_kg": 144000,
  "mean_mastery": 0.612,
  "gaps": [ /* 10 lowest-mastery standards (< 55%) */ ],
  "strengths": [ /* 8 highest-mastery standards (>= 70%) */ ],
  "recent": [ /* 8 most recently updated standards */ ],
  "grade_breakdown": { "3": {"count": 12, "mean_mastery": 0.71}, ... }
}
```

Supports optional `grade` and `subject` query params to filter the SKILL_STATE query.

### Multi-Turn Chat (`VertexLLM.chat()`)

The tutor endpoints call `llm.chat(system, history, message, model)`, which builds a native Gemini multi-turn payload:
```json
{
  "systemInstruction": {"parts": [{"text": "<system_prompt>"}]},
  "contents": [
    {"role": "user", "parts": [{"text": "..."}]},
    {"role": "model", "parts": [{"text": "..."}]},
    {"role": "user", "parts": [{"text": "<current_message>"}]}
  ],
  "generationConfig": {"temperature": 0.7, "maxOutputTokens": 4096}
}
```

Falls back to flattened single-prompt format if REST call fails. Note: `"assistant"` history role is translated to `"model"` for Gemini's API.

---

## The Shared State Object

`AssessmentState` (`agent/state.py`) flows through the entire LangGraph. Each node returns only the keys it modifies.

| Field | Type | What it holds |
|---|---|---|
| `student_id` | `str` | Student identifier |
| `grade` | `str` | Grade level (K1–K8) |
| `subject` | `str` | "math" or "english" |
| `state_jurisdiction` | `str` | US state abbrev or "Multi-State" |
| `framework` | `str` | Standards framework name |
| `theta` | `float` | Rasch ability logit (-4 to +4) |
| `theta_history` | `list[float]` | θ after each answer |
| `question_difficulties` | `dict[str, float]` | node_id → β (difficulty logit) |
| `knowledge_state` | `dict[str, float]` | node_id → inferred mastery (KST) |
| `hard_blocked_nodes` | `list[str]` | nodes locked by failed hard prereqs |
| `misconceptions` | `list[dict]` | LLM-detected misconceptions per wrong answer |
| `misconception_weights` | `dict[str, float]` | mastery penalty per standard code |
| `gaps` | `list[dict]` | ranked knowledge gaps |
| `remediation_plan` | `list[dict]` | 3 exercises per gap |
| `recommendations` | `list[dict]` | ZPD learning path (up to 5 items) |
| `mastery_updates` | `dict[str, float]` | BKT P(mastery) output per node |
| `all_nodes` | `list[dict]` | IRT-selected standards for this assessment |
| `results` | `list[dict]` | per-answer outcome enriched by Rasch + BKT |
| `score` | `float` | fraction correct 0–1 |
| `rag_context_map` | `dict` | per-node RAG context from Neo4j |
| `rag_prompt_block` | `str` | formatted text block injected into question-gen prompt |

---

## Learning Science Algorithms

### Rasch 1PL IRT (`agents/rasch.py`)

```
P(correct | θ, β) = 1 / (1 + e^-(θ-β))
Fisher Information I(θ,β) = P * (1 - P)
θ update (Newton-Raphson): θ += 0.5 * (observed - P) / I(θ,β)
  clamped to [-4.0, +4.0]
Standard Error: SE = 1 / sqrt(Σ I(θ,β))
Grade equivalent: θ → nearest grade in GRADE_DIFFICULTY map
```

- **θ (theta)** = student ability logit. Starts at 0.0 (average). Range -4 to +4.
- **β (beta)** = question difficulty logit. Grade 1 = -2.0, Grade 5 = 0.0, Grade 8 = +1.5.
- **STEP_SIZE = 0.5** — learning rate for Newton-Raphson step.
- DOK level adds offset: DOK 1 = -0.5, DOK 2 = 0.0, DOK 3 = +0.5, DOK 4 = +1.0.
- Prerequisite category subtracts 0.5 from base β.
- `RaschSession.to_dict()` returns `{theta, se, grade_equivalent, n_items, history}`.

**Grade → difficulty mapping:**
| Grade | β logit |
|---|---|
| K | -3.0 |
| 1 | -2.0 |
| 2 | -1.5 |
| 3 | -1.0 |
| 4 | -0.5 |
| 5 | 0.0 |
| 6 | +0.5 |
| 7 | +1.0 |
| 8 | +1.5 |
| 9+ | +2.0 |

### Bayesian Knowledge Tracing (`agents/evaluation_agent.py`)

Classic 4-parameter Hidden Markov model:
```python
P_INIT  = 0.10   # prior probability of mastery
P_LEARN = 0.20   # probability of learning after each attempt
P_SLIP  = 0.10   # P(wrong | mastered)
P_GUESS = 0.20   # P(correct | not mastered)

if correct:
    posterior = p_mastery * (1 - P_SLIP) / denominator
else:
    posterior = p_mastery * P_SLIP / denominator

p_after = posterior + (1 - posterior) * P_LEARN
```

**Misconception penalty applied before BKT:**
```python
p_before_adj = max(0.05, p_before - misconception_penalty)
```
This ensures LLM-detected misconceptions lower mastery before the BKT update, not independently of it.

Mastery state persisted as `SKILL_STATE` relationship edges in Neo4j after every assessment.

### Knowledge Space Theory (`agents/kst.py`)

After a ~10-question assessment, only ~10 nodes are directly observed. KST fills in the rest:

```
SUCCESS_DECAY  = 0.90   per hop downward (toward prereqs)
FAILURE_DECAY  = 0.70   per hop upward (toward advanced concepts)
MAX_HOPS       = 3
HARD_PREREQ_THRESHOLD  = 0.9   (edge weight ≥ 0.9 → hard block)
HARD_BLOCK_MASTERY     = 0.05
```

- **Success propagation (downward):** If θ≥0.6 on a node, propagate `mastery * 0.90 * edge_weight` to prereqs, up to 3 hops.
- **Failure propagation (upward):** If θ<0.4 on a node, propagate failure upward. Hard edges (weight≥0.9) → all children set to 0.05 (hard-blocked). Soft edges (weight 0.5–0.89) → penalise with 0.70 decay.
- **Misconception penalty:** Subtracts `misconception_weight` from each affected node's KST mastery.
- `identify_frontier()`: returns nodes below mastery_threshold=0.60 whose ALL prereqs are above that threshold — the ZPD "ready to learn" set.

### Maximum Information Gain (`agents/irt_selector.py`)

Select questions where `I(θ,β) = P*(1-P)` is maximised (peaks at 0.25 when θ=β):

- `rank_nodes_by_information(θ, candidates)` — returns all candidates sorted by information score.
- `select_next_node(θ, candidates, already_asked, failed_ids, prereq_map)` — single-step selector (for live adaptive sessions).
- **Intersection bonus:** nodes whose `domains` list has > 1 entry get score × 1.5.
- **Prerequisite block:** if any parent of a node is in `failed_ids`, skip that node (threshold: p_correct < 0.35).
- `assign_difficulties(nodes)` — pre-computes `{identifier: β}` for a batch.
- `build_prerequisite_map(nodes)` — builds `{child_id: [parent_ids]}` from node dicts.

---

## VertexLLM Client (`agents/vertex_llm.py`)

Three-path fallback for `generate()`:

1. **Generative Language REST** (`generativelanguage.googleapis.com`) — ADC Bearer token.
2. **Vertex AI REST** (`aiplatform.googleapis.com`) — ADC Bearer token; tries `flash_model`, then `gemini-1.5-flash-001`, then `gemini-1.5-pro-001`.
3. **google.generativeai SDK** — `GEMINI_API_KEY` from `.env`.

Raises `RuntimeError` with setup instructions if all three fail.

**`generate_json(prompt)`** — enhanced JSON generation:
1. Appends `"Return ONLY valid JSON. No markdown fences."` to prompt.
2. Tries twice (retries once on `None`).
3. If result is a dict, auto-unwraps known wrapper keys: `questions`, `items`, `data`, `results`, `assessment`, `exercises`.
4. `_parse_json()` strips markdown fences, tries `[...]` first then `{...}`.

**`chat(system, history, message, model)`** — multi-turn conversation:
- Builds native Gemini `systemInstruction` + `contents` payload.
- Temperature 0.7, maxOutputTokens 4096.
- Falls back to single flattened prompt via `generate()` if REST fails.
- Default chat model: `gemini-2.5-pro` (configurable per endpoint).

All agents call `get_llm()` which returns the module-level singleton `VertexLLM` instance.

---

## Neo4j Schema

### Node Labels
- `StandardsFrameworkItem` — one per standard (CCSS, TEKS, CA, FL, NY, GA, NC, OH, Multi-State)
- `Student` — one per student ID
- `GeneratedQuestion` — persisted generated questions (optional)

### Key Node Properties (`StandardsFrameworkItem`)
| Property | Example |
|---|---|
| `identifier` | `"ccss-math-1-nbt-b-3"` |
| `statementCode` | `"1.NBT.B.3"` |
| `description` | `"Compare two two-digit numbers…"` |
| `gradeLevelList` | `["1"]` |
| `academicSubject` | `"Mathematics"` |
| `jurisdiction` | `"Multi-State"` or `"Texas"` |
| `normalizedStatementType` | `"Standard"` |

### Relationship Types
| Relationship | Meaning |
|---|---|
| `BUILDS_TOWARDS` | Concept A is prerequisite for concept B |
| `HAS_DEPENDENCY` | Alternate prerequisite edge type |
| `DEFINES_UNDERSTANDING` | Alternate prerequisite edge type |
| `PRECEDES` | Sequential ordering (A before B) |
| `HAS_CHILD` | Hierarchical cluster → standard |
| `TESTS` | GeneratedQuestion → StandardsFrameworkItem |
| `SKILL_STATE` | Student → StandardsFrameworkItem (mastery edge) |

### `SKILL_STATE` Edge Properties
| Property | Meaning |
|---|---|
| `p_mastery` | BKT P(mastered) float 0–1 |
| `attempts` | Total attempts count |
| `correct` | Total correct count |
| `last_updated` | datetime() |

---

## API Endpoints

### Assessment (`/api/v1/assessment/`)
| Method | Path | Description |
|---|---|---|
| `POST` | `/generate` | IRT-ranked standards → Gemini questions (Phase A) |
| `POST` | `/evaluate` | Score + Rasch + BKT + KST + remediation (Phase B) |
| `GET` | `/nodes` | Preview which standards would be selected (dry run) |
| `GET` | `/grades` | List all grades, subjects, US states + frameworks |
| `GET` | `/student/{id}/performance` | BKT performance report (coverage%, mastery%, blocking gaps) |
| `GET` | `/student/{id}/trajectory` | K1–K8 grade-by-grade mastery trajectory |
| `GET` | `/recommendations/{id}` | Graph-aware recommendations (immediate actions + next standards + learning path) |

### AI Tutor (`/api/v1/chat/`)
| Method | Path | Description |
|---|---|---|
| `POST` | `/tutor` | Multi-turn chat grounded in full assessment EvalResult (Gemini 2.5 Pro) |
| `POST` | `/standalone` | Multi-turn chat grounded in live Neo4j mastery (works without assessment) |
| `GET` | `/context/{student_id}` | Load structured mastery profile from Neo4j for standalone tutor |

### Other
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/students/{id}` | Student mastery profile |
| `POST` | `/api/v1/rag/context` | GraphRAG context for a set of node IDs |
| `GET` | `/health` | Neo4j connectivity + standards count + Gemini status |

---

## `/evaluate` Response Shape

```json
{
  "assessment_id": "...",
  "student_id": "...",
  "score": 0.750,
  "correct": 9,
  "total": 12,
  "grade_status": "at",          // "above" | "at" | "approaching" | "below"
  "prerequisite_score": 0.833,
  "target_score": 0.714,
  "theta": 0.412,                // Rasch ability logit
  "theta_history": [0.0, 0.3, 0.5, ...],
  "gap_count": 3,
  "gaps": [{
    "node_identifier": "...",
    "code": "3.NF.A.1",
    "description": "...",
    "mastery_prob": 0.21,
    "hard_blocked": false,
    "downstream_blocked": 4,
    "priority": "medium"
  }],
  "hard_blocked_count": 1,
  "gap_exercises": [{
    "standard_code": "...",
    "concept_explanation": "...",
    "misconception": "...",
    "exercises": [{"order": 1, "type": "word_problem", "question": "...", "hint": "...", "answer": "..."}]
  }],
  "misconceptions": [{
    "question_id": "...",
    "standard_code": "...",
    "misconception": "Student confuses numerator and denominator",
    "affected_standards": ["3.NF.A.1"],
    "mastery_penalty": 0.2
  }],
  "recommendations": [{
    "rank": 1,
    "standard_code": "...",
    "description": "...",
    "why_now": "...",
    "how_to_start": "...",
    "estimated_minutes": 30,
    "difficulty": "accessible",
    "success_prob": 0.52,
    "information_score": 0.2499
  }],
  "bkt_updates": [{"node": "...", "mastery": 0.61}],
  "results": [/* per-question detail */]
}
```

---

## Settings Knobs (`.env`)

| Key | Default | Effect |
|---|---|---|
| `GEMINI_API_KEY` | `""` | API key auth for Gemini (fallback path 3) |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Model used for question generation + misconceptions |
| `GCP_PROJECT_ID` | `homeschoollms` | Vertex AI project (ADC auth, paths 1+2) |
| `GCP_REGION` | `us-central1` | Vertex AI region |
| `AGENT_MAX_QUESTIONS` | `10` | Max standards/questions per assessment (latency control) |
| `AGENT_MASTERY_THRESHOLD` | `0.7` | BKT mastery threshold |
| `AGENT_GAP_LIMIT` | `5` | Max gaps to remediate per session |
| `RAG_ENABLED` | `true` | Enable GraphRAG context injection |
| `RAG_GRAPH_HOP_DEPTH` | `4` | Max prerequisite hops in RAG context |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j connection |
| `DATABASE_URL` | `postgresql+asyncpg://…` | Postgres connection |
| `CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowed origins |

---

## Θ Bootstrapping — `_load_student_theta`

Before every Phase A or Phase B call, the API loads the student's current θ from Neo4j:

```python
masteries = [r.p_mastery for r in SKILL_STATE edges]
mean_p = avg(masteries), clamped to [0.01, 0.99]
theta  = log(mean_p / (1 - mean_p))   # logit transform
theta  = clamp(theta, -4.0, 4.0)
# New students → return 0.0 (average ability)
```

This means returning students start each assessment from their true estimated ability, not zero.

---

## AI Tutor — Tutoring Guidelines (built into every system prompt)

The tutor system prompt enforces these rules on every response:
1. Be warm, encouraging, and specific — always reference exact standard code and concept.
2. Use age-appropriate language for the student's grade level.
3. When explaining a gap, say WHY the concept matters + give a real-world example.
4. When explaining a misconception, gently clarify what the student likely misunderstood.
5. When giving next steps, suggest 1–2 concrete practice activities.
6. Keep responses focused — avoid unnecessary filler.
7. May use **bold**, bullet points, and numbered lists for clarity.
8. If asked a math/ELA question directly, work through it step-by-step.
9. Stay grounded in this student's actual results — no generic advice.
10. If the student seems discouraged, acknowledge effort and reframe mistakes as learning.

---

## Restart Checklist

```bash
# 1. Start Docker Desktop (Neo4j + Postgres)
docker compose -f infra/compose.yaml down
docker compose -f infra/compose.yaml up -d

# 2. Start backend (Poetry venv)
/Users/esendashnyam/Library/Caches/pypoetry/virtualenvs/adaptive-learning-engine-tZmjiD0W-py3.14/bin/uvicorn \
  backend.app.main:app --reload --host 0.0.0.0 --port 8000

# 3. Start frontend
cd frontend && npm run dev

# 4. Health check
curl http://localhost:8000/health
```

---

## Project Structure

```
adaptive-learning-engine/
├── backend/
│   └── app/
│       ├── agent/              ← legacy (dormant, keep for reference)
│       │   ├── state.py        ← shared AssessmentState schema (still imported by new system)
│       │   ├── graph.py        ← old LangGraph
│       │   └── nodes.py        ← old node functions
│       ├── agents/             ← active multi-agent system
│       │   ├── orchestrator.py       — LangGraph: Phase A + Phase B graphs
│       │   ├── assessment_agent.py   — select_standards_irt, fetch_rag_context, generate_questions
│       │   ├── evaluation_agent.py   — score_answers, update_rasch, detect_misconceptions, update_bkt
│       │   ├── gap_agent.py          — identify_and_rank_gaps, route_after_gaps
│       │   ├── remediation_agent.py  — generate_remediation (3 exercises per gap)
│       │   ├── recommendation_agent.py — generate_recommendations (ZPD frontier)
│       │   ├── rasch.py              — Rasch 1PL IRT math + RaschSession
│       │   ├── kst.py                — KST propagation + identify_frontier
│       │   ├── irt_selector.py       — rank_nodes_by_information, assign_difficulties
│       │   └── vertex_llm.py         — VertexLLM: generate, generate_json, chat
│       ├── api/routes/
│       │   ├── assessment.py   ← main assessment endpoints
│       │   ├── chat.py         ← AI Tutor: /tutor, /standalone, /context
│       │   ├── students.py
│       │   ├── rag.py
│       │   ├── agent.py
│       │   └── rasch.py
│       ├── core/settings.py    ← all config from .env
│       ├── db/                 ← Postgres models + async engine
│       ├── student/            ← legacy AssessmentEngine + BayesianSkillTracker
│       └── main.py             ← FastAPI app + lifespan + router registration
├── frontend/
│   └── app/
│       ├── assessment/page.tsx ← main assessment UI + AI Tutor chat
│       ├── dashboard/          ← student mastery dashboard
│       └── rasch/              ← IRT diagnostic page
├── infra/
│   └── compose.yaml            ← Neo4j + Postgres Docker containers
├── scripts/
│   ├── import_learning_commons.py      ← import standards into Neo4j
│   └── enrich_prerequisite_edges.py
├── data/
│   └── learning-commons-kg/            ← exported JSONL nodes/relationships
├── pyproject.toml
└── .env                                ← secrets (gitignored)
```
