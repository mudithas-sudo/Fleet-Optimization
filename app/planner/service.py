"""Programmatic invocation of the planner agent from FastAPI."""

import json

from ..agent_runtime import AgentRun
from .agent import planner_agent

_run = AgentRun("fleet-plan", planner_agent)


async def plan_route(stops: list[dict], avoid_tolls: bool = False,
                     vehicle_type: str = "car") -> dict:
    """Run the planner agent over the stops and return the route dict for the trip.

    Raises RuntimeError if the agent/tool failed to produce a route.
    """
    # avoid_tolls / stops go through session state so the Routes tool reads
    # them directly — never depending on the LLM forwarding them correctly
    state = await _run(
        json.dumps({"stops": stops, "avoidTolls": avoid_tolls,
                    "vehicleType": vehicle_type}),
        {"avoid_tolls": avoid_tolls, "vehicle_type": vehicle_type, "stops": stops})

    raw = state.get("routes_api_raw")
    if not raw:
        raise RuntimeError("Route computation failed - no route data produced.")

    plan = state.get("route_plan") or {}
    briefing = plan.get("briefing") if isinstance(plan, dict) else None

    return {
        **raw,
        "briefing": briefing or "Route planned. Follow the stops in the listed order.",
    }
