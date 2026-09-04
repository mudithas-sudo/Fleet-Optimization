"""Route planner agent — invoked programmatically, not a chatbot."""

import os

from google.adk import Agent
from pydantic import BaseModel, Field

from .tools import compute_routes


class RoutePlan(BaseModel):
    ordered_stop_labels: list[str] = Field(
        description="All stop labels in final drive order: origin first, then the "
                    "optimized intermediate stops, then the destination.")
    briefing: str = Field(
        description="A 3-5 sentence friendly driver briefing: stop order, total "
                    "distance and time, and anything notable such as a long leg.")


planner_agent = Agent(
    name="route_planner",
    model=os.environ.get("MODEL", "gemini-3.5-flash-lite"),
    description="Plans an optimized fleet route through a list of stops.",
    instruction=(
        "You are a fleet route planner. The user message is a JSON object with a "
        "list of stops: the first is the origin, the last is the destination, and "
        "the rest are intermediate stops. Call compute_routes exactly once, passing "
        "the stops list as JSON. From its result, produce the final answer: the "
        "stop labels in optimized drive order and a concise, friendly driver "
        "briefing covering the stop order, total distance and time (use km and "
        "minutes), and any notably long leg. Times are live-traffic estimates: "
        "if traffic_delay_minutes is 5 or more, warn about the current traffic "
        "delay. If estimated_toll_price is set, state the toll cost; if the "
        "request has avoidTolls true, note that the route avoids toll roads. "
        "Address the driver appropriately for the request's vehicleType (e.g. "
        "rider for a bike, truck driver for a truck). Do not invent numbers - "
        "use only the tool's values."
    ),
    tools=[compute_routes],
    output_schema=RoutePlan,
    output_key="route_plan",
)
