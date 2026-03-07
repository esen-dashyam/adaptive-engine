# Adaptive Learning Engine

An adaptive K1–K8 assessment platform that combines a 144,000-node curriculum knowledge graph, item response theory, Bayesian knowledge tracing, and Google Gemini to deliver personalized assessments, identify learning gaps, and tutor students through a conversational AI interface.

---

## What it does

1. **Adaptive Assessment (Phase A)** — Selects curriculum standards from Neo4j ranked by IRT Fisher Information at the student's current ability level (θ). Gemini generates fresh multiple-choice questions grounded in the graph's prerequisite chains and sibling standards (GraphRAG). No static question bank.

2. **Deep Evaluation (Phase B)** — Scores answers with Rasch 1PL IRT to update θ, runs Bayesian Knowledge Tracing to update per-standard mastery probabilities, detects misconceptions, propagates mastery through the knowledge graph via KST, ranks learning gaps by downstream blocking impact, and generates targeted remediation exercises.

3. **AI Tutor** — After results load, a Gemini-powered chat window opens automatically. The tutor is grounded in the full evaluation result: score, θ, every gap with its mastery probability, recommended learning path, and remediation exercises. Students can ask follow-up questions in natural language.

4. **Recommendation Engine** — Identifies the Zone of Proximal Development frontier in the knowledge graph and returns the 3–5 next concepts optimally positioned between the student's current ability and the next challenge level.

---

## How the knowledge graph drives everything

The Neo4j database holds **144,733 StandardsFrameworkItem** nodes spanning K–12 Mathematics and English Language Arts for all 50 US states plus Multi-State (CCSS) standards.

Each node is connected by typed edges that encode curriculum dependencies:

| Edge type | Meaning | Weight property |
|-----------|---------|-----------------|
| `PRECEDES` | This standard comes before the next | — |
| `BUILDS_TOWARDS` | Conceptual prerequisite | `conceptual_weight` |
| `DEFINES_UNDERSTANDING` | Understanding dependency | `understanding_strength` |
| `HAS_CHILD` | Parent→child cluster | — |

Student mastery is stored on `SKILL_STATE` edges between `Student` nodes and `StandardsFrameworkItem` nodes:

```cypher
(:Student)-[:SKILL_STATE {
  p_mastery,    // Bayesian posterior P(student has mastered this)
  p_transit,    // P(learning on next attempt)
  p_slip,       // P(knows but answers wrong)
  p_guess,      // P(doesn't know but guesses right)
  attempts,
  correct
}]->(:StandardsFrameworkItem)
```

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11, FastAPI, LangGraph |
| AI | Google Gemini (gemini-2.5-pro / gemini-2.0-flash) via Vertex AI |
| Knowledge graph | Neo4j 5 |
| Relational DB | PostgreSQL 15 |
| Frontend | Next.js 14, TypeScript, TailwindCSS |
| Infrastructure | Docker Compose |

---

## Quick start

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
# Fill in GEMINI_API_KEY, NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
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

Open http://localhost:3000

---

## API reference

### Assessment

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/assessment/generate` | Phase A: select standards + generate questions |
| `POST` | `/api/v1/assessment/evaluate` | Phase B: score + BKT + KST + gaps + remediation + recommendations |

### AI Tutor

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/chat/tutor` | Multi-turn chat grounded in a full EvalResult payload |
| `POST` | `/chat/standalone` | Multi-turn chat grounded in live Neo4j mastery profile |
| `GET`  | `/chat/context/{student_id}` | Load student mastery profile for standalone tutor |

### Example: generate an assessment

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

### Example: submit answers

```bash
curl -X POST http://localhost:8000/api/v1/assessment/evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "student_id": "student_001",
    "grade": "3",
    "subject": "math",
    "state_jurisdiction": "TX",
    "answers": {"<question_id>": "A", ...},
    "questions": [...]
  }'
```

---

## Learning science

### Rasch 1PL Item Response Theory

Every question has a difficulty parameter β (logit scale). The student's latent ability is θ. The probability of a correct answer is:

```
P(correct | θ, β) = 1 / (1 + exp(-(θ - β)))
```

Fisher Information is maximized when θ ≈ β (50% success probability), so the engine selects questions with β closest to the student's current θ to get the most diagnostic signal per question.

### Bayesian Knowledge Tracing (BKT)

Parameters: P_init=0.10, P_learn=0.20, P_slip=0.10, P_guess=0.20

```
P(L | correct) = P(L)*(1-slip) / [P(L)*(1-slip) + (1-P(L))*guess]
P(L | wrong)   = P(L)*slip     / [P(L)*slip     + (1-P(L))*(1-guess)]
P(L_next)      = P(L|obs) + (1 - P(L|obs)) * P_learn
```

### Knowledge Space Theory (KST) propagation

After scoring, mastery propagates through the prerequisite graph:
- **Success propagates forward** (child→parent): mastery * 0.90 decay per hop
- **Failure propagates backward** (parent→child): mastery * 0.70 penalty per hop
- **Hard block**: if a prerequisite edge has `conceptual_weight >= 0.9` and the student failed, all downstream nodes are marked hard-blocked

---

## Environment variables

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

## Project structure

```
adaptive-learning-engine/
├── backend/
│   └── app/
│       ├── agents/
│       │   ├── assessment_agent.py      # Phase A: IRT selection + GraphRAG + generation
│       │   ├── evaluation_agent.py      # Phase B: scoring, Rasch, BKT, misconceptions
│       │   ├── gap_agent.py             # KST propagation + gap ranking
│       │   ├── remediation_agent.py     # Gap exercise generation
│       │   ├── recommendation_agent.py  # ZPD frontier + learning path
│       │   ├── orchestrator.py          # LangGraph pipeline wiring
│       │   ├── irt_selector.py          # Fisher Information ranking
│       │   ├── rasch.py                 # Rasch 1PL model
│       │   ├── bkt.py                   # Bayesian Knowledge Tracing
│       │   ├── kst.py                   # Knowledge Space Theory
│       │   └── vertex_llm.py            # Gemini client (GenAI + Vertex fallback)
│       ├── api/routes/
│       │   ├── assessment.py            # /generate + /evaluate endpoints
│       │   └── chat.py                  # AI tutor endpoints
│       ├── agent/state.py               # LangGraph AssessmentState schema
│       ├── db/models/                   # SQLAlchemy ORM models
│       └── core/settings.py             # Pydantic settings
├── frontend/
│   └── app/
│       └── assessment/page.tsx          # Single-page assessment + chat UI
├── infra/
│   └── compose.yaml                     # Neo4j + PostgreSQL
└── ARCHITECTURE.md                      # Full system architecture reference
```

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the complete system design including all Neo4j node labels, relationship types, PostgreSQL schema, and the full LangGraph pipeline.
