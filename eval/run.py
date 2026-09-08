"""Run the ADK agent eval sets.

    FAKE_ROUTES=1 GOOGLE_API_KEY=... venv/bin/python eval/run.py

Uses google.adk.evaluation.AgentEvaluator: it replays each eval case (session
state + user message), lets the real agent run, and scores the tool trajectory
and the final response against the reference. Needs a Gemini key for the agent
*and* the response judge. FAKE_ROUTES keeps the compute_routes tool off Google.
"""

import asyncio
import os
import pathlib
import sys

os.environ.setdefault("FAKE_ROUTES", "1")
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from google.adk.evaluation import AgentEvaluator  # noqa: E402

EVAL_SETS = [
    ("app.planner.agent", ROOT / "eval" / "planner_pickup.evalset.json"),
    # ("app.dispatcher.agent", ...)  # needs a seeded DB — see README
]


async def main() -> int:
    if not (os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")):
        print("Set GOOGLE_API_KEY (or GEMINI_API_KEY) to run the eval.")
        return 2
    failures = 0
    for module, path in EVAL_SETS:
        print(f"\n=== {module}  ·  {path.name} ===")
        try:
            await AgentEvaluator.evaluate(
                agent_module=module,
                eval_dataset_file_path_or_dir=str(path),
                num_runs=1,
            )
        except AssertionError as e:
            failures += 1
            print(f"FAILED: {e}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
