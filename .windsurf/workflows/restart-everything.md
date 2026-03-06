---
description: restart all local adaptive-learning-engine services (Neo4j, Postgres, backend, frontend)
auto_execution_mode: 3
---

1. Make sure Docker Desktop is running
   - Confirm Docker is up so the Neo4j/Postgres containers can start.

2. Stop existing backend and frontend dev servers
   - From any terminal, run:
     - `pkill -f "uvicorn.*backend.app.main:app" || true`
     - `pkill -f "next dev" || true`

3. Restart infrastructure services (Neo4j + Postgres)
   - Set the working directory to the repo root:
     - `/Users/esendashnyam/Desktop/adaptive-learning-engine`
   - Then run:
     - `docker compose -f infra/compose.yaml down`
     - `docker compose -f infra/compose.yaml up -d`
   - Wait until containers report `healthy` in `docker ps`.

4. Start the FastAPI backend (Adaptive Learning Engine API)
   - Working directory: `/Users/esendashnyam/Desktop/adaptive-learning-engine`
   - Run:
     - `/Users/esendashnyam/Library/Caches/pypoetry/virtualenvs/adaptive-learning-engine-tZmjiD0W-py3.14/bin/uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000`
   - If the venv path changes (e.g. after `poetry install`), find it with: `poetry env info --path`
   - Prefer to run this in a dedicated terminal pane.

5. Start the Next.js frontend
   - Working directory: `/Users/esendashnyam/Desktop/adaptive-learning-engine/frontend`
   - Run:
     - `npm install` (first time only)
     - `npm run dev`
   - Frontend will be available at `http://localhost:3000`.

6. Quick health checks
   - Backend health:
     - `curl http://localhost:8000/health`
   - GraphRAG status (optional):
     - `curl http://localhost:8000/api/v1/rag/context -X POST -H "Content-Type: application/json" -d '{"identifiers":[],"max_prereqs":4}'`
   - Frontend:
     - Open `http://localhost:3000` in a browser.
