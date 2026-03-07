# Adaptive Learning Engine — Full Architecture

## Conceptual Model

The engine is a **Hybrid Cognitive System** — two "brains" working together:

| System | Role | Analogy |
|---|---|---|
| **Rasch IRT + BKT + KST** | Measures, calculates, and tracks with precision | The Cerebellum — balance, weights, calibrated path |
| **Gemini (LLM)** | Reads the student's "vibe", interprets chat, diagnoses misconceptions | The Sensory Nervous System — qualitative signal interpreter |
| **φ Signal Bridge** | Translates LLM qualitative output into a BKT numeric delta | The missing link connecting the two brains |

The φ (Fidelity Factor) is the central innovation: instead of the LLM and the algorithm running in parallel and ignoring each other, φ is the number that lets Gemini's reading of the student's chat directly modify the BKT mastery update equation. Every node in the LangGraph pipeline either produces a signal or consumes one.

---

## System Topology

```
Student (browser)
    │
    ▼
Next.js Frontend (localhost:3000)
    │
    │  POST /api/v1/assessment/generate        Phase A: adaptive question set
    │  POST /api/v1/assessment/evaluate        Phase B: full 20-node pipeline
    │  POST /api/v1/assessment/exercise_complete   live exercise BKT update + EMA
    │  POST /api/v1/assessment/exercise_chat   live chat → φ signal → recursive pivot
    │  POST /api/v1/chat/tutor                 post-assessment AI Tutor
    │  POST /api/v1/chat/standalone            always-on AI Tutor
    │  GET  /api/v1/chat/context/{id}          load mastery profile
    │
    ▼
FastAPI Backend (localhost:8000)
    │
    ├─ Phase A LangGraph (4 nodes) ──────────► Neo4j KG + Gemini Flash
    ├─ Phase B LangGraph (20 nodes) ─────────► Neo4j KG + Gemini Flash + Postgres
    ├─ exercise_chat endpoint ───────────────► Neo4j + Gemini Flash (real-time)
    └─ AI Tutor (chat.py) ───────────────────► Neo4j KG + Gemini 2.5 Pro
```

---

## Infrastructure

| Layer | Technology | What it stores |
|---|---|---|
| **Graph DB** | Neo4j 5 (Docker `ale-neo4j`) | 144K+ `StandardsFrameworkItem` nodes; `SKILL_STATE`, `TEMPORARY_BLOCK`, `ATTEMPTED` edges per student; `BUILDS_TOWARDS`/`PRECEDES` edges with learned `conceptual_weight` |
| **Relational DB** | PostgreSQL 15 (Docker `ale-postgres`) | `AssessmentSession`, `AssessmentAnswer`, `ChatSession`, `FailureChain` tables |
| **LLM** | Gemini 2.0 Flash (pipeline) + Gemini 2.5 Pro (tutor) via Vertex AI REST / GenAI SDK | Question generation, Dynamic Weight Auditor (φ), misconception diagnosis, remediation, bridge instructions, tutoring |
| **Backend** | Python 3.10+, FastAPI, LangGraph, Poetry | Orchestrates all agents |
| **Frontend** | Next.js 14, TypeScript, TailwindCSS | Assessment UI, results, chat, parent dashboard |

---

## Phase A — Adaptive Question Generation (4 nodes)

```
API call: POST /generate  {grade, subject, student_id, state, num_questions}
                │
                ▼
   _load_student_theta
      MATCH (s:Student)-[r:SKILL_STATE]->()
      mean_p = avg(r.p_mastery) → θ = logit(mean_p)
      New students → θ = 0.0
                │
                ▼
   [1] select_standards_irt
      Cypher query: StandardsFrameworkItem nodes
        WHERE gradeLevelList = grade
          AND academicSubject = subject
          AND jurisdiction = state
          AND normalizedStatementType = 'Standard'
          AND NOT n.identifier IN $already_asked        ← elastic stopping exclusion
          AND NOT EXISTS {                               ← cognitive load pruning
            MATCH (:Student {id:$sid})-[:TEMPORARY_BLOCK]->(n)
          }
      + prerequisite grade (grade-1) nodes
      + BUILDS_TOWARDS / HAS_DEPENDENCY / DEFINES_UNDERSTANDING edges
      Fisher Information ranking: I(θ,β) = P*(1-P)  where P = sigmoid(θ-β)
      Multi-domain nodes get 1.5× intersection bonus
                │
                ▼
   [2] fetch_rag_context
      Per node: prereqs (BUILDS_TOWARDS → n),
                sibling standards (same grade+subject),
                existing question stems (GeneratedQuestion → TESTS → n)
      Builds rag_prompt_block injected into Gemini prompt
                │
                ▼
   [3] generate_questions
      Gemini Flash → JSON array of N questions
      A/B/C/D options, correct answer, dok_level,
      standard_code, node_index for node_ref linkage
      Visual/image questions blocked (text-only enforcement)

Output: assessment_id, questions[], theta, framework, core_standards
```

---

## Phase B — Full Evaluation Pipeline (20 nodes)

```
API call: POST /evaluate  {assessment_id, student_id, answers[], confusion_signal?, total_answered_prior?}

  ┌─────────────────────────────────────────────────────────────────────────┐
  │  ENTRY                                                                   │
  │  [1] detect_confusion_signal                                             │
  │      Checks state.confusion_signal flag (set when student types          │
  │      "I don't get this" in chat without completing answers)              │
  └───────────┬─────────────────────────────────────────────────────────────┘
              │
    ┌─────────┴────────────────┐
    │ confused=True            │ confused=False
    ▼                          ▼
  [2a] lca_confusion        [3] score_answers
       BFS backward via           Compare submitted vs correct
       BUILDS_TOWARDS to          Capture time_ms per question
       nearest mastered           Flag is_likely_guess (correct + <4s)
       ancestor (p≥0.95)          Include chat_message per answer
       → lca_safety_nets{}        → results[], score, time_per_question{}
          │                            │
          ▼                            ▼
      write_report ←         [4] chat_to_signal  ← THE SIGNAL BRIDGE
      (early exit with            Dynamic Weight Auditor (Gemini)
       scaffold anchor)           For each answered question:
                                  Analyzes: chat_message + time_ms + correctness + dok
                                  Outputs: φ ∈ [-1.0, 1.0] per question_id
                                  φ = 1.0  Fluent (genuine understanding)
                                  φ = 0.5  Partial (hesitant / "I think...")
                                  φ = 0.2  Brittle (very fast, likely guess)
                                  φ = 0.0  Neutral (wrong, BKT handles)
                                  φ = -0.5 Struggling (specific hurdle)
                                  φ = -1.0 Hard Block ("I don't get this")
                                  Fallback: heuristic from time_ms if LLM fails
                                  → phi_signals{}, session_context[]
                                       │
                                       ▼
                               [5] update_rasch
                                   RaschSession(initial_theta=θ)
                                   For each answer in order:
                                     θ = update_theta(θ, β, is_correct)
                                   Produces: theta, theta_history[], se, total_answered
                                       │
                                       ▼
                               [6] check_stopping_criterion
                                   Elastic Stopping Gate:
                                   SE = 1 / sqrt(Σ I(θ,β_i))
                                       │
                          ┌────────────┴─────────────┐
                   SE ≥ 0.3                      SE < 0.3
                   AND count < 25              OR count ≥ 25
                          │                          │
                          ▼                          ▼
           [7a] generate_follow_up_questions    [7b] detect_misconceptions
                Calls Phase A agents inline          Wrong answers → Gemini Flash
                Excludes already-asked nodes         "What misconception led to this?"
                Returns FOLLOWUP_BATCH=5 new         Returns [{standard_code,
                questions                             misconception, root_prereq_code,
                needs_more_questions=True             affected_standards[], penalty}]
                → additional_questions[]             Back-propagates to root prereq
                → write_report (partial)             nodes in Neo4j
                                                          │
                                                          ▼
                                                    [8] lca_misconception
                                                        BFS backward for each
                                                        root_prerequisite_code
                                                        → lca_safety_nets{}
                                                             │
                                                             ▼
                                                    [9] update_bkt  ← φ-MODIFIED FORMULA
                                                        For each tested node:
                                                          φ = phi_signals[qid].phi
                                                          p_before_adj -= misconception_penalty
                                                          TRADITIONAL BKT POSTERIOR:
                                                            P(L|correct) = P(L)(1-slip)/denom
                                                            P(L|wrong)   = P(L)slip/denom
                                                          φ-MODULATED TRANSITION:
                                                            P(L_{t+1}) = P(L|obs)
                                                                       + (1-P(L|obs))*(p_transit*φ)
                                                          φ<0 → un-learning (mastery decreases)
                                                          Writes p_mastery to Neo4j SKILL_STATE
                                                          Logs phi per result
                                                             │
                                                             ▼
                                                    [10] consolidate_memory
                                                         Persist GeneratedQuestion nodes
                                                         (:Student)-[:ATTEMPTED]->(:GeneratedQuestion)
                                                                  -[:TESTS]->(:StandardsFrameworkItem)
                                                         EMA update on BUILDS_TOWARDS edges:
                                                           new_weight = old*0.95 + signal*0.05
                                                             │
                                                             ▼
                                                    [11] load_exercise_memory
                                                         Fetch prior exercise history
                                                         from Neo4j for all assessed standards
                                                         → exercise_memory{}
                                                             │
                                                             ▼
                                                    [12] identify_and_rank_gaps
                                                         Fetch 2-hop KG subgraph
                                                         KST propagation on subgraph
                                                         Gaps = nodes where KST < 0.55
                                                         Rank: hard-blocked → downstream → mastery
                                                         Cap at 8 gaps
                                                         COGNITIVE LOAD PRUNING:
                                                           For each hard-blocked node:
                                                             MATCH downstream *1..4 hops
                                                             CREATE (:Student)-[:TEMPORARY_BLOCK
                                                               {blocked_by, created_at}]->(:SFI)
                                                           → newly_blocked_nodes[]
                                                             │
                                              ┌─────────────┴─────────────┐
                                          has gaps                    no gaps
                                              │                           │
                                              ▼                           ▼
                                    [13] generate_remediation        skip to [14]
                                         Per gap (up to 5):
                                         Injects NanoPoint metadata tag:
                                           [NanoPoint_ID: {node_id} |
                                            Standard: {code} |
                                            Difficulty: {mastery:.2f} |
                                            DOK: {dok_target}]
                                         DOK 1: foundational re-teaching
                                         DOK 2: guided practice / application
                                         Informs Gemini of prior exercises
                                           (avoids repetition)
                                         Returns 3 exercises per gap with
                                           hint, answer, explanation
                                              │
                                              ▼
                                    [14] judge_mastery  (both paths converge)
                                         Gemini holistic mastery verdict
                                         per standard. Considers:
                                           BKT before/after
                                           This session's answers
                                           Full exercise history trend
                                           DOK level of correct vs incorrect
                                         Returns mastery_verdicts{}:
                                           {verdict, confidence, reasoning,
                                            next_action, override_mastery}
                                              │
                                              ▼
                                    [15] apply_fidelity_correction
                                         Neuro-Symbolic fidelity pass.
                                         For each correct answer:
                                           signal_1 (time): is_likely_guess → factor=0.5
                                           signal_2 (LLM): struggling verdict → factor=0.5
                                                           mastered+challenge → factor=1.2
                                         Applies factor only to the GAIN:
                                           corrected = p_before + gain * factor
                                         Writes corrected p_mastery to Neo4j
                                           with fidelity_factor property
                                              │
                                              ▼
                                    [16] generate_recommendations
                                         KST frontier identification:
                                           unmastered nodes whose ALL prereqs
                                           ARE mastered (ZPD boundary)
                                         Scores frontier by I(θ,β)
                                           prefer β ≈ θ (50% success zone)
                                         Gemini: why_now, how_to_start,
                                           estimated_minutes, difficulty
                                              │
                                              ▼
                                    [17] llm_recommendation_decider
                                         Final LLM pass:
                                           Filter already-mastered concepts
                                           Reprioritize: remediate first
                                           Add decision_reasoning per item
                                           Produce session_narrative (2 sentences)
                                           Pick focus_concept
                                              │
                                              ▼
                                    [18] write_report → END
                                         Logs final summary
                                         Returns {} (state already populated)
```

---

## Live Exercise Session — The "Back and Forth"

### `POST /assessment/exercise_chat`

The real-time signal bridge for remediation exercises. Called whenever the student types in the chatbox during an exercise.

```
Frontend sends:
  {student_id, node_identifier, standard_code, concept,
   exercise_text, nanopoint_tag, chat_message,
   answer?, correct?, time_ms, beta}
        │
        ▼
   Step 1 — Compute φ (Gemini Dynamic Weight Auditor)
     Prompt includes: exercise context, nanopoint_tag, chat_message, time_ms
     Returns: {phi, reason, gap_tag}
     Falls back to time heuristic if LLM fails
        │
        ▼
   Step 2 — φ-Modified BKT Update (immediate write to Neo4j)
     P(L_{t+1}) = P(L|obs) + (1 - P(L|obs)) * (p_transit * φ)
     When φ = -1.0:
       P(L_{t+1}) = P(L|obs) - (1 - P(L|obs)) * p_transit
       Mastery actively pulled DOWN — un-learning
        │
        ▼
   Step 3 — Recursive Pivot (if φ < -0.3)
     The "Back":
       compute_pivot() calls find_lca(driver, student_id, node_identifier)
       BFS backward through BUILDS_TOWARDS (up to 6 hops)
       Finds nearest mastered ancestor (p_mastery ≥ 0.95)
     The "Bridge":
       Gemini generates 2-3 sentence bridge instruction:
         "Good news — you already understand {anchor}!
          Let's use that knowledge as our starting point to figure out {target}.
          Ready to try again?"
     The "Forth":
       After student nails the pivot exercise (φ → 1.0),
       frontend resumes original exercise with bridge framing
        │
        ▼
   Step 4 — FailureChain audit (if φ < -0.3)
     INSERT into PostgreSQL failure_chains:
       {student_id, failed_node_id, failed_node_code,
        root_prereq_node_id, root_prereq_code,
        signal_source: "phi_negative", hops_to_lca}
        │
        ▼
   Returns:
     {phi, reason, gap_tag,
      p_mastery_before, p_mastery_after,
      pivot_needed, pivot_node, bridge_instruction}
```

### Chat Signal Reference Table

| Student input | φ | BKT effect | Action |
|---|---|---|---|
| "Oh! I see, just multiply the base" | 1.0 | Full gain | Continue |
| Correct at reasonable speed | 0.7 | Standard gain | Continue |
| "I think it's this?" | 0.5 | Half gain | Continue (brittle flag) |
| Correct in < 3s on DOK ≥ 2 | 0.2 | Tiny gain | Flag for fidelity review |
| Wrong answer, no chat | 0.0 | BKT posterior handles loss | Continue |
| "I got the first part but not second" | -0.5 | Un-learning starts | Note partial gap |
| "I don't get why the 4 moved there" | -1.0 | Active un-learning | **Trigger recursive pivot** |

---

## The φ-Modified BKT Formula

### Standard BKT (old)

```
P(L_{t+1}) = P(L_t|Obs) + (1 - P(L_t|Obs)) * p_transit
```

### Neuro-Symbolic BKT (new)

```
Step 1 — Bayes posterior (unchanged):
  if correct: P(L|obs) = P(L)(1-slip)  / [P(L)(1-slip) + (1-P(L))guess]
  if wrong:   P(L|obs) = P(L)slip      / [P(L)slip + (1-P(L))(1-guess)]

Step 2 — φ-modulated transition:
  P(L_{t+1}) = P(L_t|Obs) + (1 - P(L_t|Obs)) * (p_transit * φ)

  φ = 1.0  → full transition gain (genuine mastery signal)
  φ = 0.5  → half gain (brittle mastery)
  φ = 0.0  → no transition (mastery frozen at posterior)
  φ = -1.0 → P(L_{t+1}) = P(L|obs) - (1 - P(L|obs)) * p_transit
              (un-learning: mastery pulled below the posterior)

Step 3 — Clamped to [0.01, 0.999]
```

### φ Sources (signal priority)

1. **chat_to_signal** (Gemini Dynamic Weight Auditor): reads `chat_message`, `time_ms`, `correctness` per question → primary φ source during assessment Phase B
2. **exercise_chat** (inline Gemini call): reads live chat during a remediation exercise → real-time φ for the Back-and-Forth
3. **Heuristic fallback**: `is_likely_guess` flag from `score_answers` (correct in < 4s) → φ = 0.5; normal correct → φ = 1.0; wrong → φ = 0.0

---

## Elastic Stopping (Computerized Adaptive Testing)

The assessment does not always ask a fixed number of questions. It stops when the Standard Error of θ drops below the precision threshold.

```
SE = 1 / sqrt(Σ I(θᵢ, βᵢ))    where I(θ,β) = P(1-P)

After each answer batch:
  if SE < 0.30 OR total_answered ≥ 25:
    → continue to full Phase B evaluation
  else:
    → generate_follow_up_questions (5 more, excluding already-asked)
    → return needs_more_questions: true + additional_questions[] to frontend
    → frontend submits next batch with total_answered_prior count
```

Phase A's `select_standards_irt` always excludes nodes already asked this session (`NOT n.identifier IN $asked_ids`) so follow-up questions are always novel.

---

## Cognitive Load Pruning (TEMPORARY_BLOCK)

When `identify_and_rank_gaps` finds a hard block (failed prereq with `conceptual_weight ≥ 0.9`), it writes block relationships to all downstream nodes:

```cypher
MATCH (s:Student {id: $sid})
MATCH (blocker:StandardsFrameworkItem {identifier: $nid})
    -[:BUILDS_TOWARDS|PRECEDES*1..4]->(downstream)
MERGE (s)-[b:TEMPORARY_BLOCK]->(downstream)
ON CREATE SET b.blocked_by = $nid, b.created_at = $now
```

**Effect:** Next time the student takes an assessment, `select_standards_irt` filters these out. The student cannot be presented with concepts they literally cannot access yet. This prevents the "Overwhelmed" student state.

**Unblocking:** When the student masters the blocking node via `exercise_complete` (p_mastery ≥ 0.65):

```cypher
MATCH (s:Student {id: $sid})-[b:TEMPORARY_BLOCK]->(n)
WHERE b.blocked_by = $blocker_nid
DELETE b
```

---

## Fidelity Correction Layer

`apply_fidelity_correction` runs after `judge_mastery` and applies a final correction to the BKT gain that was already written by `update_bkt`. It corrects for over-crediting.

```python
For each correct answer result:
  factor = 1.0  # default

  # Signal 1 — time-based
  if is_likely_guess (correct + < 4s + DOK≥2):
    factor = min(factor, 0.5)

  # Signal 2 — LLM verdict
  if judge_mastery verdict="struggling" and is_correct:
    factor = min(factor, 0.5)   # correct answer was a fluke
  elif judge_mastery verdict="mastered" and next_action="challenge":
    factor = max(factor, 1.2)   # genuine mastery, small bonus

  corrected_mastery = p_before + (p_after - p_before) * factor
  → written to Neo4j SKILL_STATE with fidelity_factor property
```

This is distinct from the φ correction in `update_bkt` — φ modifies the *transition formula*, fidelity correction adjusts the *final persisted value* after LLM review.

---

## Learning Science Algorithms

### Rasch 1PL IRT (`agents/rasch.py`)

```
P(correct | θ, β) = 1 / (1 + e^-(θ-β))
Fisher Information: I(θ,β) = P * (1 - P)     ← peaks at 0.25 when θ=β
θ update (Newton-Raphson): θ += 0.5 * (observed - P) / I(θ,β)
  clamped to [-4.0, +4.0]
Standard Error: SE = 1 / sqrt(Σ I(θᵢ,βᵢ))
```

**Grade → β difficulty map:**

| Grade | β logit | | Grade | β logit |
|---|---|---|---|---|
| K | -3.0 | | 5 | 0.0 |
| 1 | -2.0 | | 6 | +0.5 |
| 2 | -1.5 | | 7 | +1.0 |
| 3 | -1.0 | | 8 | +1.5 |
| 4 | -0.5 | | 9+ | +2.0 |

DOK offset: DOK1 = -0.5, DOK2 = 0.0, DOK3 = +0.5, DOK4 = +1.0.
Prerequisite category subtracts 0.5 from base β.

### Bayesian Knowledge Tracing (BKT)

Parameters (system defaults; per-skill values fitted by Baum-Welch EM override these):

```
P_INIT    = 0.10   prior probability of mastery
P_TRANSIT = 0.10   probability of learning on next attempt
P_SLIP    = 0.08   P(wrong | mastered)
P_GUESS   = 0.25   P(correct | not mastered)
```

Per-skill parameters are stored on `StandardsFrameworkItem` nodes as `bkt_p_slip`, `bkt_p_guess`, `bkt_p_transit` after Baum-Welch fitting (see `student/bkt_fitter.py`).

The misconception penalty is applied before the BKT update:
```python
p_before_adj = max(0.05, p_before - misconception_penalty)
```

### Knowledge Space Theory — KST (`agents/kst.py`)

```
SUCCESS_DECAY  = 0.90   per hop downward (toward prereqs)
FAILURE_DECAY  = 0.70   per hop upward (toward advanced concepts)
MAX_HOPS       = 3
HARD_PREREQ_THRESHOLD  = 0.9   (edge weight ≥ 0.9 → hard block)
HARD_BLOCK_MASTERY     = 0.05
MASTERY_GAP_THRESHOLD  = 0.55  (below this = gap)
```

After a ~10-question assessment, only ~10 of 144K nodes are directly observed. KST fills in the rest by propagating signals through the `BUILDS_TOWARDS`/`PRECEDES` graph. `identify_frontier()` finds the ZPD: unmastered nodes whose ALL prerequisites ARE mastered.

### LCA — Lowest Common Ancestor (`agents/lca_agent.py`)

```cypher
MATCH path = (ancestor)-[:BUILDS_TOWARDS*1..6]->(target_node)
MATCH (student)-[:SKILL_STATE]->(ancestor)
WHERE sk.p_mastery >= 0.95
ORDER BY length(path) ASC LIMIT 1
```

Returns the nearest mastered ancestor within 6 hops. Used by:
- `lca_confusion` — when student sends confusion signal mid-assessment
- `lca_misconception` — after misconceptions are detected
- `compute_pivot()` — inside `exercise_chat` for recursive pivot

---

## AssessmentState — Full Field Reference (`agent/state.py`)

| Field | Type | Populated by | Description |
|---|---|---|---|
| `student_id` | `str` | API | |
| `grade` | `str` | API | Grade level (K1–K8) |
| `subject` | `str` | API | "math" or "english" |
| `state_jurisdiction` | `str` | API | US state or "Multi-State" |
| `theta` | `float` | `update_rasch` | Rasch ability logit |
| `theta_history` | `list[float]` | `update_rasch` | θ after each answer |
| `se` | `float` | `update_rasch` | Standard Error of θ estimate |
| `total_answered` | `int` | `update_rasch` | Cumulative questions answered |
| `needs_more_questions` | `bool` | `generate_follow_up` | Elastic stopping flag |
| `additional_questions` | `list[dict]` | `generate_follow_up` | Follow-up questions for frontend |
| `confusion_signal` | `bool` | API | Student sent "I don't get this" |
| `confusion_chat` | `str` | API | Raw chat message from student |
| `lca_safety_nets` | `dict[str, Any]` | `lca_confusion` / `lca_misconception` | code → nearest mastered ancestor |
| `phi_signals` | `dict[str, dict]` | `chat_to_signal` | question_id → {phi, reason, gap_tag, target_node} |
| `session_context` | `list[dict]` | `chat_to_signal` | Accumulated chat messages with φ |
| `pivot_node` | `str \| None` | `exercise_chat` | Safety-net node identifier |
| `pivot_instruction` | `str` | `exercise_chat` | Bridge text for recursive pivot |
| `time_per_question` | `dict[str, float]` | `score_answers` | question_id → time_ms |
| `questions` | `list[dict]` | Phase A | IRT-selected questions |
| `submitted_answers` | `list[dict]` | API | Student's submitted answers |
| `results` | `list[dict]` | `score_answers`+others | Per-answer enriched outcomes |
| `score` | `float` | `score_answers` | Fraction correct 0–1 |
| `misconceptions` | `list[dict]` | `detect_misconceptions` | LLM-detected root misconceptions |
| `misconception_weights` | `dict[str, float]` | `detect_misconceptions` | Standard code → mastery penalty |
| `mastery_updates` | `dict[str, float]` | `update_bkt` | node_id → p_mastery after φ-BKT |
| `mastery_verdicts` | `dict[str, dict]` | `judge_mastery` | code → {verdict, confidence, next_action, ...} |
| `llm_decisions` | `dict[str, Any]` | `llm_recommendation_decider` | session_narrative, focus_concept, ... |
| `knowledge_state` | `dict[str, float]` | `identify_and_rank_gaps` | node_id → KST-inferred mastery |
| `hard_blocked_nodes` | `list[str]` | `identify_and_rank_gaps` | Nodes locked by hard prereqs |
| `newly_blocked_nodes` | `list[str]` | `identify_and_rank_gaps` | Nodes just given TEMPORARY_BLOCK |
| `gaps` | `list[dict]` | `identify_and_rank_gaps` | Ranked knowledge gaps |
| `remediation_plan` | `list[dict]` | `generate_remediation` | 3 exercises per gap + nanopoint_tag |
| `recommendations` | `list[dict]` | `generate_recommendations` | ZPD learning path |
| `exercise_memory` | `dict[str, list]` | `load_exercise_memory` | code → prior exercise history |

---

## Neo4j Graph Schema

### Node Labels

| Label | Count | Purpose |
|---|---|---|
| `StandardsFrameworkItem` | 144,733 | Every curriculum standard (CCSS, TEKS, CA, FL, NY, GA, NC, OH, Multi-State) |
| `LearningComponent` | 3,805 | Cluster/domain grouping nodes |
| `GeneratedQuestion` | growing | Questions generated + persisted by the system |
| `Student` | 1+ | One node per student ID |
| `RaschSession` | growing | IRT session snapshots |

### Relationship Types

#### Curriculum edges (connect `StandardsFrameworkItem` nodes)

| Relationship | Count | Key Properties | Meaning |
|---|---|---|---|
| `BUILDS_TOWARDS` | 5,301 | `conceptual_weight` (learned via EMA) | A is prerequisite for B. Primary edge for IRT, KST, LCA |
| `DEFINES_UNDERSTANDING` | 102,368 | `conceptual_weight`, `understanding_strength` | A must be understood to understand B |
| `PRECEDES` | varies | — | Ordering within grade level |
| `HAS_CHILD` | 25,740 | — | Cluster/domain → standard |

The `conceptual_weight` on `BUILDS_TOWARDS` is continuously learned by the EMA update in `consolidate_memory`:
```python
# Signal = 1.0 when prereq mastery predicts target mastery; 0.2 when not
new_weight = old_weight * 0.95 + signal * 0.05
```

#### Student-specific edges

| Relationship | Connects | Properties |
|---|---|---|
| `SKILL_STATE` | `Student → StandardsFrameworkItem` | `p_mastery`, `p_slip`, `p_guess`, `p_transit`, `attempts`, `correct`, `last_updated`, `fidelity_factor` |
| `TEMPORARY_BLOCK` | `Student → StandardsFrameworkItem` | `blocked_by` (node_identifier that caused block), `created_at`, `updated_at` |
| `ATTEMPTED` | `Student → GeneratedQuestion` | `correct`, `selected_answer`, `correct_answer`, `timestamp`, `session_id` |

#### Question edges

| Relationship | Connects | Meaning |
|---|---|---|
| `TESTS` | `GeneratedQuestion → StandardsFrameworkItem` | Links question to the standard it tests |

---

## PostgreSQL Schema

### `failure_chains` — Immutable φ Audit Log

Every time a student's φ drops below -0.3 in a live exercise session:

| Column | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `student_id` | VARCHAR(128) | |
| `failed_node_id` | VARCHAR(512) | Neo4j identifier of failed concept |
| `failed_node_code` | VARCHAR(64) | Standard code e.g. "5.NBT.A.1" |
| `root_prereq_node_id` | VARCHAR(512) | LCA result node identifier |
| `root_prereq_code` | VARCHAR(64) | LCA result standard code |
| `signal_source` | VARCHAR(30) | "phi_negative", "misconception", "consecutive_struggles", "silence" |
| `hops_to_lca` | INT | Hops from failed node to safety net |
| `recorded_at` | TIMESTAMPTZ | Auto |

### `chat_sessions` — Tutor Working Memory

One row per student, upserted on each tutor interaction:

| Column | Type | Notes |
|---|---|---|
| `student_id` | VARCHAR(128) UNIQUE | |
| `current_node_id` | VARCHAR(512) | Neo4j node currently in focus |
| `current_node_code` | VARCHAR(64) | Standard code |
| `pedagogical_strategy` | VARCHAR(20) | "socratic" → "visual" → "cra" (escalates on struggles) |
| `consecutive_struggles` | INT | Resets on success |
| `last_message_at` | TIMESTAMPTZ | Used for silence detection (>120s) |

### Other tables

| Table | Purpose |
|---|---|
| `students` | One row per student, stores external_id + cached θ |
| `mastery_records` | Postgres audit copy of Neo4j SKILL_STATE |
| `assessment_sessions` | One row per completed assessment run, stores gap_analysis JSONB |
| `assessment_answers` | One row per answered question, stores mastery_before/after |

---

## API Endpoints — Complete Reference

### Assessment (`/api/v1/assessment/`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/generate` | Phase A: IRT-ranked standards → Gemini questions |
| `POST` | `/evaluate` | Phase B: full 20-node pipeline |
| `POST` | `/exercise_complete` | Live exercise submission: φ-BKT update + EMA edge weight + unblock check |
| `POST` | `/exercise_chat` | Live chat → φ computation → recursive pivot if needed |
| `GET` | `/nodes` | Dry-run: preview which standards would be selected |
| `GET` | `/grades` | List all grades, subjects, US states |
| `GET` | `/student/{id}/performance` | BKT performance report |
| `GET` | `/student/{id}/trajectory` | K1–K8 grade trajectory |
| `GET` | `/readiness/{id}` | LLM assessment readiness check |

### AI Tutor (`/api/v1/chat/`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/tutor` | Multi-turn chat grounded in full EvalResult (Gemini 2.5 Pro) |
| `POST` | `/standalone` | Multi-turn chat grounded in live Neo4j mastery |
| `GET` | `/context/{student_id}` | Load structured mastery profile for standalone tutor |

---

## `/evaluate` Request + Response Shape

### Request additions (new fields)

```json
{
  "assessment_id": "...",
  "student_id": "...",
  "grade": "K5",
  "subject": "math",
  "state": "Multi-State",
  "total_answered_prior": 0,
  "confusion_signal": false,
  "confusion_chat": "",
  "answers": [
    {
      "question_id": "...",
      "question": "...",
      "options": ["A. ...", "B. ...", "C. ...", "D. ..."],
      "correct_answer": "B",
      "student_answer": "B",
      "time_ms": 8200,
      "chat_message": "I think it's B because...",
      "category": "target",
      "dok_level": 2,
      "standard_code": "5.NBT.A.1",
      "node_ref": "2e68b6d0-...",
      "beta": 0.0
    }
  ]
}
```

### Response (complete shape)

```json
{
  "assessment_id": "...",
  "student_id": "...",
  "score": 0.500,
  "correct": 5,
  "total": 10,
  "grade_status": "below",
  "prerequisite_score": 0.667,
  "target_score": 0.429,

  "theta": -1.842,
  "theta_history": [0.0, -0.5, -0.8, ...],
  "se": 0.412,
  "total_answered": 10,

  "needs_more_questions": false,
  "additional_questions": [],

  "gap_count": 5,
  "gaps": [{
    "node_identifier": "...",
    "code": "5.NBT.A.1",
    "description": "...",
    "grade": "5",
    "mastery_prob": 0.12,
    "hard_blocked": true,
    "downstream_blocked": 8,
    "priority": "high"
  }],
  "hard_blocked_count": 2,
  "newly_blocked_nodes": ["...", "..."],

  "gap_exercises": [{
    "node_identifier": "...",
    "standard_code": "...",
    "nanopoint_tag": "[NanoPoint_ID: ... | Standard: ... | Difficulty: 0.12 | DOK: 1]",
    "concept_explanation": "...",
    "misconception": "...",
    "exercises": [{"order": 1, "type": "word_problem", "question": "...", "hint": "...", "answer": "..."}]
  }],

  "misconceptions": [{
    "question_id": "...",
    "standard_code": "...",
    "misconception": "...",
    "root_prerequisite_code": "4.NBT.A.1",
    "affected_standards": ["5.NBT.A.1"],
    "mastery_penalty": 0.25
  }],

  "recommendations": [{
    "rank": 1,
    "node_identifier": "...",
    "standard_code": "...",
    "description": "...",
    "grade": "4",
    "difficulty_beta": -0.5,
    "success_prob": 0.52,
    "current_mastery": 0.18,
    "why_now": "...",
    "how_to_start": "...",
    "estimated_minutes": 30,
    "difficulty": "accessible",
    "information_score": 0.2499,
    "llm_action": "remediate",
    "decision_reasoning": "...",
    "llm_priority": "high"
  }],

  "bkt_updates": [{"node": "...", "mastery": 0.166}],
  "mastery_verdicts": {
    "5.NBT.A.1": {
      "verdict": "struggling",
      "confidence": 0.9,
      "reasoning": "...",
      "next_action": "remediate",
      "override_mastery": null
    }
  },
  "session_narrative": "...",
  "focus_concept": "5.NBT.A.1",
  "lca_safety_nets": {
    "5.NBT.A.1": {"node_id": "...", "code": "4.NBT.A.1", "description": "...", "hops": 2, "p_mastery": 0.97}
  },
  "results": [{
    "question_id": "...",
    "question": "...",
    "options": ["A. ...", ...],
    "correct_answer": "B",
    "student_answer": "A",
    "is_correct": false,
    "is_likely_guess": false,
    "time_ms": 8200.0,
    "chat_message": "I think it's A",
    "category": "target",
    "dok_level": 2,
    "standard_code": "5.NBT.A.1",
    "node_ref": "...",
    "beta": 0.0,
    "theta_before": 0.0,
    "theta_after": -0.5,
    "mastery_before": 0.72,
    "mastery_after": 0.166,
    "phi": -0.5
  }]
}
```

---

## Project Structure (current)

```
adaptive-learning-engine/
├── backend/
│   └── app/
│       ├── agent/
│       │   └── state.py               ← AssessmentState Pydantic model (all pipeline fields)
│       ├── agents/
│       │   ├── orchestrator.py        ← Phase A (4 nodes) + Phase B (20 nodes) LangGraph graphs
│       │   ├── assessment_agent.py    ← select_standards_irt, fetch_rag_context, generate_questions
│       │   ├── evaluation_agent.py    ← score_answers (+ time/chat capture), update_rasch (+ SE),
│       │   │                              detect_misconceptions, update_bkt (φ-formula)
│       │   ├── signal_bridge.py       ← chat_to_signal, compute_pivot, write_failure_chain
│       │   ├── adaptive_agents.py     ← detect_confusion_signal, lca_safety_net,
│       │   │                              check_stopping_criterion, generate_follow_up_questions
│       │   ├── gap_agent.py           ← identify_and_rank_gaps (+ TEMPORARY_BLOCK pruning)
│       │   ├── remediation_agent.py   ← generate_remediation (+ NanoPoint metadata tags)
│       │   ├── metacognitive_agent.py ← judge_mastery, apply_fidelity_correction,
│       │   │                              llm_recommendation_decider
│       │   ├── recommendation_agent.py ← generate_recommendations (ZPD frontier)
│       │   ├── memory_agent.py        ← consolidate_memory (+ EMA), load_exercise_memory
│       │   ├── lca_agent.py           ← find_lca (BFS backward to nearest mastered ancestor)
│       │   ├── rasch.py               ← Rasch 1PL IRT math + RaschSession (with SE property)
│       │   ├── kst.py                 ← KST propagation + identify_frontier
│       │   ├── irt_selector.py        ← rank_nodes_by_information, select_next_node
│       │   └── vertex_llm.py          ← VertexLLM: generate, generate_json, chat
│       ├── api/routes/
│       │   ├── assessment.py          ← /generate, /evaluate, /exercise_complete, /exercise_chat
│       │   └── chat.py                ← /tutor, /standalone, /context
│       ├── student/
│       │   ├── bkt_fitter.py          ← Baum-Welch EM for per-skill BKT parameter fitting
│       │   ├── bayesian_tracker.py    ← BayesianSkillTracker (legacy, used by rasch route)
│       │   └── rasch_engine.py        ← RaschEngine (legacy, used by /rasch route)
│       ├── db/
│       │   ├── base.py                ← SQLAlchemy engine + Base
│       │   └── models/
│       │       ├── __init__.py        ← exports all models
│       │       ├── student.py         ← Student, MasteryRecord, AssessmentSession, AssessmentAnswer
│       │       └── chat.py            ← ChatSession, FailureChain
│       ├── core/settings.py           ← all config from .env
│       └── main.py                    ← FastAPI app, lifespan, router registration
├── frontend/
│   └── app/
│       ├── assessment/page.tsx        ← main assessment UI + AI Tutor chat
│       ├── dashboard/                 ← parent/teacher dashboard
│       └── rasch/                     ← IRT diagnostic view
├── infra/
│   └── compose.yaml                   ← Neo4j + PostgreSQL Docker containers
├── scripts/
│   ├── import_learning_commons.py
│   └── enrich_prerequisite_edges.py
└── tests/
    ├── backend/tests/test_assessment_pipeline.py   ← 88 unit tests
    └── frontend/__tests__/assessment_logic.test.ts  ← 72 Jest tests
```

---

## Restart Checklist

```bash
# 1. Docker (Neo4j + Postgres)
docker compose -f infra/compose.yaml up -d

# 2. Backend (Poetry venv)
poetry run uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000

# 3. Frontend
cd frontend && npm run dev

# 4. Health check
curl http://localhost:8000/health
```

---

## Environment Variables

| Key | Default | Effect |
|---|---|---|
| `GEMINI_API_KEY` | `""` | Gemini API key auth (fallback if ADC unavailable) |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Model for pipeline nodes (question gen, φ auditor, misconceptions, remediation) |
| `GCP_PROJECT_ID` | — | Vertex AI project (ADC auth) |
| `GCP_REGION` | `us-central1` | Vertex AI region |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j connection |
| `NEO4J_USER` | `neo4j` | Neo4j auth |
| `NEO4J_PASSWORD` | — | Neo4j auth |
| `DATABASE_URL` | `postgresql+asyncpg://…` | Postgres async connection |
| `AGENT_MAX_QUESTIONS` | `10` | Max questions per initial assessment batch |
| `AGENT_MASTERY_THRESHOLD` | `0.7` | BKT mastery threshold |
| `AGENT_GAP_LIMIT` | `5` | Max gaps to remediate per session |
