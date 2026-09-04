"""Programmatic invocation of the planner agent from FastAPI."""

import json
import uuid

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .agent import planner_agent

APP_NAME = "fleet"
USER_ID = "admin"

_session_service = InMemorySessionService()
_runner = Runner(app_name=APP_NAME, agent=planner_agent, session_service=_session_service)


async def plan_route(stops: list[dict], avoid_tolls: bool = False,
                     vehicle_type: str = "car") -> dict:
    """Run the planner agent over the stops and return the route dict for the trip.

    Raises RuntimeError if the agent/tool failed to produce a route.
    """
    session_id = f"plan-{uuid.uuid4().hex[:8]}"
    # avoid_tolls goes through session state so the Routes tool reads it
    # directly - it never depends on the LLM forwarding it correctly
    await _session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=session_id,
        state={"avoid_tolls": avoid_tolls, "vehicle_type": vehicle_type,
               "stops": stops})

    message = types.Content(
        role="user",
        parts=[types.Part(text=json.dumps({
            "stops": stops, "avoidTolls": avoid_tolls, "vehicleType": vehicle_type}))])

    async for _event in _runner.run_async(
            user_id=USER_ID, session_id=session_id, new_message=message):
        pass

    session = await _session_service.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=session_id)

    raw = session.state.get("routes_api_raw")
    if not raw:
        raise RuntimeError("Route computation failed - no route data produced.")

    plan = session.state.get("route_plan") or {}
    briefing = plan.get("briefing") if isinstance(plan, dict) else None

    return {
        **raw,
        "briefing": briefing or "Route planned. Follow the stops in the listed order.",
    }
