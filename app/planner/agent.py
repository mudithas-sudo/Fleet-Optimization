"""Route planner agent — invoked programmatically, not a chatbot."""

import os

from google.adk import Agent
from pydantic import BaseModel, Field

from .tools import compute_routes


class RoutePlan(BaseModel):
    ordered_stop_labels: list[str] = Field(
        description="All stop labels in final drive order, exactly as returned "
                    "by compute_routes (origin first, destination last).")
    briefing: str = Field(
        description="A 3-5 sentence friendly driver briefing: the stop order in "
                    "plain language (say 'collect' / 'drop off' for pickup and "
                    "delivery stops), total distance and time, any notably long "
                    "leg, and any stop the tool marks atRisk.")


planner_agent = Agent(
    name="route_planner",
    model=os.environ.get("MODEL", "gemini-3.5-flash-lite"),
    description="Plans an optimized fleet route through a list of stops.",
    instruction=(
        "You are a fleet route planner. The user message is a JSON object with a "
        "list of stops: the first is the origin (depot). Each non-depot stop has "
        "a `kind` — \"delivery\" or \"pickup\" — and a `parcelId`. A parcel with "
        "a pickup has one \"pickup\" stop and one \"delivery\" stop sharing its "
        "parcelId; pickup stops may carry `earliest`/`latest` (epoch seconds).\n"
        "\n"
        "Call compute_routes exactly once.\n"
        "- If NO stop is a pickup: pass order_json as \"\" (empty) — the route "
        "is optimised automatically.\n"
        "- If ANY stop is a pickup: YOU choose the visiting order. Pass it as "
        "order_json: a JSON array of \"<parcelId>:<kind>\" strings, depot "
        "excluded, every non-depot stop listed exactly once. Rules: a parcel's "
        "pickup MUST come before its delivery; try to reach each pickup within "
        "its [earliest, latest] window; otherwise keep total driving low by "
        "grouping nearby stops.\n"
        "\n"
        "From the tool result produce the final answer: `ordered_stop_labels` "
        "copied verbatim from the tool, and a concise, friendly briefing "
        "covering the stop order (collect / drop off), total distance and time "
        "(km and minutes), any long leg, and — reading the tool's `stops` list "
        "— any stop with atRisk true (say which and why: tight window or "
        "deadline). If traffic_delay_minutes is 5 or more, warn about the "
        "traffic delay. If estimated_toll_price is set, state it; if avoidTolls "
        "is true, note the route avoids tolls. Address the driver for the "
        "vehicleType (rider for a bike, etc.). Do not invent numbers — use only "
        "the tool's values."
    ),
    tools=[compute_routes],
    output_schema=RoutePlan,
    output_key="route_plan",
)
