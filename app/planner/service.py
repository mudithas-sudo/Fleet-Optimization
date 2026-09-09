"""Programmatic invocation of the planner agent from FastAPI."""

import json
import time

from ..agent_runtime import AgentRun
from .agent import planner_agent

_run = AgentRun("fleet-plan", planner_agent)


def _clock(ts: float | None) -> str | None:
    if not ts:
        return None
    try:
        return time.strftime("%I:%M %p", time.localtime(ts)).lstrip("0")
    except (ValueError, OSError, OverflowError):
        return None   # far-future sentinel / out-of-range — treat as "no time"


def _agent_view(stops: list[dict]) -> list[dict]:
    """A clean, ID-free description of the stops for the LLM. Ordering is done
    with the opaque `ref` string (kept out of prose by the instruction); the
    briefing quotes `parcel` / `at` only."""
    view = []
    for i, s in enumerate(stops):
        kind = s.get("kind") or ("depot" if s.get("depot") else "delivery")
        if i == 0 or kind == "depot":
            view.append({"action": "start", "at": s.get("label", "the depot")})
            continue
        row = {
            "ref": f"{s.get('parcelId')}:{kind}",
            "action": "collect" if kind == "pickup" else "drop off",
            "parcel": s.get("parcelName") or s.get("label"),
            "at": s.get("place") or s.get("label"),
        }
        horizon = time.time() + 400 * 86400   # ignore "no deadline" sentinels
        if kind == "pickup":
            if _clock(s.get("earliest")):
                row["collectNoEarlierThan"] = _clock(s.get("earliest"))
            if _clock(s.get("latest")):
                row["collectNoLaterThan"] = _clock(s.get("latest"))
        elif s.get("deadline") and s["deadline"] < horizon and _clock(s.get("deadline")):
            row["deliverBy"] = _clock(s.get("deadline"))
        view.append(row)
    return view


async def plan_route(stops: list[dict], avoid_tolls: bool = False,
                     vehicle_type: str = "car") -> dict:
    """Run the planner agent over the stops and return the route dict for the trip.

    Raises RuntimeError if the agent/tool failed to produce a route.
    """
    # avoid_tolls / stops go through session state so the Routes tool reads
    # them directly — never depending on the LLM forwarding them correctly
    state = await _run(
        json.dumps({"stops": _agent_view(stops), "avoidTolls": avoid_tolls,
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
