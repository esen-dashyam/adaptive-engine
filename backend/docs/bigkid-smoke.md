# Big-Kid Backend — Local Smoke Recipes

Start: `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`

State: `curl -H "X-Child-Id: 11111111-1111-1111-1111-111111111111" http://localhost:8000/api/v1/child/state`

Trigger reflection: `curl -X POST http://localhost:8000/api/v1/parent/reflection/trigger -H "Content-Type: application/json" -d '{"child_id":"11111111-1111-1111-1111-111111111111","reason":"smoke test"}'`

Reset state: kill and restart uvicorn (in-memory store).

## End-to-end task + bypass smoke

1. Tap task → photo → submit → evidence flagged `submitted`.
2. `POST /parent/task/{id}/review {"decision":"approve"}` → status flips to `done`.
3. Tap task → "I couldn't do this" → submit → bypass `pending`.
4. Tap same task → submit photo → bypass auto `withdrawn`.
