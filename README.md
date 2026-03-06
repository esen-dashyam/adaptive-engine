# Adaptive Learning Engine

K1-K8 adaptive assessment platform powered by **Neo4j GraphRAG**, **Bayesian Knowledge Tracing (BKT)**, and **Google Gemini AI**.

No static question banks. Every question is generated on-the-fly by Gemini, grounded in curriculum context retrieved directly from the Neo4j knowledge graph.

---

## Architecture

```
Neo4j Knowledge Graph (242K+ StandardsFrameworkItem nodes)
  │
  ├── 1. Node Selection
  │       BKT mastery loaded from SKILL_STATE edges
  │       ZPD-tier selection (STRUGGLING > DEVELOPING > UNKNOWN > PROFICIENT)
  │
  ├── 2. GraphRAG Retrieval  ← the "RAG" layer
  │       For each selected node, Cypher queries retrieve:
  │         • prerequisite chain (DEFINES_UNDERSTANDING / BUILDS_TOWARDS)
  │         • forward progression (what this standard leads to)
  │         • domain/cluster parent + sibling standards
  │         • existing GeneratedQuestion bank (diversity constraints)
  │         • full-text related standards (FTS index)
  │
  ├── 3. Gemini Generation (RAG-augmented)
  │       KG context injected into batch prompt → per-standard fallback
  │       Questions grounded in actual curriculum structure, not just
  │       the one-line standard description
  │
  ├── 4. Persist to Neo4j
  │       (:GeneratedQuestion)-[:TESTS_STANDARD]->(:StandardsFrameworkItem)
  │
  └── 5. Evaluation → Map results back to KG
          BKT SKILL_STATE edges updated: P(mastery), nano_weight, attempts
          Gap exercises generated (Gemini + RAG context for remediation)
```

**Stack:**
- `FastAPI` — backend REST API
- `Neo4j` — knowledge graph (standards + student SKILL_STATE + question bank)
- `Gemini AI` — RAG-augmented question + remediation exercise generation
- `Next.js 14` + `TailwindCSS` — frontend
- `Docker Compose` — Neo4j + Postgres

---

## Quick Start

### 1. Clone & set up environment

```bash
cp .env.example .env
# Edit .env — add your GEMINI_API_KEY (free at https://aistudio.google.com/app/apikey)
```

### 2. Start Neo4j

```bash
docker compose -f infra/compose.yaml up neo4j -d
```

### 3. Start the backend

```bash
poetry install
poetry run uvicorn backend.app.main:app --reload --port 8000
```

### 4. Start the frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/assessment/generate` | Generate adaptive assessment |
| `POST` | `/api/v1/assessment/evaluate` | Score + update BKT + get gap exercises |
| `GET`  | `/api/v1/assessment/nodes` | Preview selected standards |
| `GET`  | `/api/v1/assessment/grades` | List grades, subjects, states |
| `GET`  | `/api/v1/assessment/student/{id}/performance` | BKT performance report |
| `GET`  | `/api/v1/assessment/student/{id}/trajectory` | K1-K8 grade trajectory |
| `POST` | `/api/v1/students/` | Register student |
| `GET`  | `/api/v1/students/{id}` | Skill profile |
| `GET`  | `/api/v1/students/{id}/gaps` | Blocking knowledge gaps |
| `GET`  | `/api/v1/students/{id}/nano-weights` | Nano weights for grade/subject |

Full interactive docs: http://localhost:8000/docs

---

## Generate an Assessment

```bash
curl -X POST http://localhost:8000/api/v1/assessment/generate \
  -H "Content-Type: application/json" \
  -d '{
    "grade": "K3",
    "subject": "math",
    "student_id": "student_001",
    "state": "TX",
    "num_questions": 15
  }'
```

---

## How BKT Works

Each student-standard pair has a `SKILL_STATE` relationship in Neo4j:

```cypher
(:Student)-[:SKILL_STATE {
  p_mastery,    // P(student knows this standard)
  p_transit,    // P(learning on each attempt)
  p_slip,       // P(knows but answers wrong)
  p_guess,      // P(doesn't know but guesses right)
  nano_weight,  // p_mastery * 100 (0-100 scale)
  attempts,
  correct
}]->(:StandardsFrameworkItem)
```

After each assessment, BKT posteriors are updated using:
- **Correct**: `P(L|correct) = P(L)*(1-P(S)) / [P(L)*(1-P(S)) + (1-P(L))*P(G)]`
- **Incorrect**: `P(L|incorrect) = P(L)*P(S) / [P(L)*P(S) + (1-P(L))*(1-P(G))]`
- **Transition**: `P(L_next) = P(L|obs) + (1 - P(L|obs)) * P(T)`

---

## ZPD Adaptive Selection

Questions are selected from tiers based on each student's current mastery:

| Tier | P(mastery) | Priority |
|------|-----------|----------|
| STRUGGLING | p < 0.35, attempts > 0 | 🔴 Highest |
| DEVELOPING | 0.35 ≤ p < 0.65 | 🟡 High |
| UNKNOWN | attempts = 0 | 🟢 Medium |
| PROFICIENT | 0.65 ≤ p < 0.85 | 🔵 Low |
| MASTERED | p ≥ 0.85 | ⚫ Filler only |

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `NEO4J_URI` | Neo4j connection URI |
| `NEO4J_USER` | Neo4j username |
| `NEO4J_PASSWORD` | Neo4j password |
| `GEMINI_API_KEY` | Gemini API key (aistudio.google.com) |
| `GEMINI_MODEL` | Model name (default: `gemini-2.0-flash`) |
| `GCP_PROJECT_ID` | GCP project (Vertex AI fallback) |
