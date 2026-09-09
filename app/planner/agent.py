"""Route planner agent — invoked programmatically, not a chatbot."""

import os

from google.adk import Agent
from google.adk.planners import PlanReActPlanner
from pydantic import BaseModel, Field

from .tools import compute_routes


class RoutePlan(BaseModel):
    ordered_stops: list[str] = Field(
        description="The final drive order as friendly one-line strings, copied "
                    "verbatim from the compute_routes `ordered_stops` field.")
    briefing: str = Field(
        description="A 3-5 sentence friendly driver briefing: the stop order in "
                    "plain language (say 'collect' / 'drop off' for pickup and "
                    "delivery stops, naming the parcel and the place), total "
                    "distance and time, any notably long leg, and any stop the "
                    "tool marks atRisk. Never contains an internal ref, a "
                    "parcel id, or coordinates.")


planner_agent = Agent(
    name="route_planner",
    model=os.environ.get("MODEL", "gemini-3.5-flash-lite"),
    description="Plans an optimized fleet route through a list of stops.",
    instruction=(
        "You are a fleet route planner. The user message is a JSON object with a "
        "`stops` list. The first stop is `action: \"start\"` (the depot). Every "
        "other stop has `action` (\"collect\" or \"drop off\"), `parcel` (its "
        "name), `at` (the place), an opaque `ref` string, and — for collections "
        "— optional `collectNoEarlierThan` / `collectNoLaterThan` times.\n"
        "\n"
        "Call compute_routes exactly once.\n"
        "- If NO stop is a collection: pass order_json as \"\" (empty) — the "
        "route is optimised automatically.\n"
        "- If ANY stop is a collection: YOU choose the visiting order. Pass it "
        "as order_json: a JSON array of the `ref` strings, depot excluded, every "
        "non-depot stop listed exactly once. Rules: a parcel's collection MUST "
        "come before its drop off; try to reach each collection within its "
        "time window; otherwise keep total driving low by grouping nearby "
        "stops.\n"
        "\n"
        "From the tool result produce the final answer: `ordered_stops` copied "
        "verbatim from the tool's `ordered_stops`, and a concise, friendly "
        "briefing covering the stop order (say 'collect <parcel> at <place>' / "
        "'drop off <parcel> at <place>'), total distance and time (km and "
        "minutes), any long leg, and — reading the tool's `stops` list — any "
        "stop with atRisk true (say which and why: tight window or deadline). "
        "If traffic_delay_minutes is 5 or more, warn about the traffic delay. "
        "If estimated_toll_price is set, state it; if avoidTolls is true, note "
        "the route avoids tolls. Address the driver for the vehicleType (rider "
        "for a bike, etc.).\n"
        "\n"
        "NEVER write a `ref`, a parcel id (e.g. 'p-ab12cd'), or coordinates in "
        "the briefing — refer to parcels by `parcel` and locations by `at`. Do "
        "not invent numbers — use only the tool's values."
    ),
    # ReAct planning: the model lays out a plan (which stops, in what order,
    # why) before it calls compute_routes — helps the pickup→delivery
    # sequencing hold together. Tools run in the thought loop; RoutePlan is
    # enforced only on the final answer.
    planner=PlanReActPlanner(),
    tools=[compute_routes],
    output_schema=RoutePlan,
    output_key="route_plan",
)

# `adk run` / `adk web` / `adk eval` look for `root_agent`
root_agent = planner_agent
