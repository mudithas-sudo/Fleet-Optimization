# Agent evals

ADK eval sets for the LLM agents. Two layers of testing in this repo:

| | what | needs a key? | run |
|---|---|---|---|
| `tests/test_pickup_delivery.py` | deterministic logic — precedence ordering/repair, ETAs, progress transitions, abort → redelivery, step collapse | no | `venv/bin/python tests/test_pickup_delivery.py` |
| `eval/` (this dir) | the agents themselves — does the planner sequence pickups before deliveries and narrate them | **yes** — Gemini for the agent *and* the response judge | see below |

## Run the evals

```bash
FAKE_ROUTES=1 GOOGLE_API_KEY=<your key> venv/bin/python eval/run.py
```

`FAKE_ROUTES=1` keeps `compute_routes` off the Google Routes API; the agent and
`response_evaluation_score` judge still call Gemini.

`AgentEvaluator` replays each case in `planner_pickup.evalset.json` — it seeds
the session `state` (including `stops`, which the tool reads), sends the user
message, runs the real `root_agent`, and scores against `test_config.json`:

- `tool_trajectory_avg_score` — did it call `compute_routes`
- `response_evaluation_score` — is the briefing coherent (1–5, judge)
- `response_match_score` — overlap with the reference briefing

The hard guarantee (a pickup is never routed after its delivery) is enforced in
code by `routing.repair_precedence` and covered by the deterministic tests — the
eval checks that the *agent* proposes a sane order and explains it.

## Adding the dispatcher eval

`dispatcher` tools read the DB (`list_pending_parcels`, `evaluate_route`), so a
dispatcher eval set needs parcels/vehicles/drivers seeded first (point `DB_PATH`
at a temp file and insert fixtures before `AgentEvaluator.evaluate`). Not wired
up yet.

## Editing eval sets

`*.evalset.json` follows the ADK `EvalSet` schema (`eval_set_id`, `name`,
`eval_cases[]` with `conversation`, `session_input.state`). Regenerate
interactively with `adk web` → *Eval* tab, or hand-edit.
