"""compute_routes ADK function tool — wraps app.routing.compute_route.

The tool returns a SMALL dict to the model (order, legs, totals) and stashes
the full route (polylines, decoded path, steps) in
tool_context.state["routes_api_raw"] so trusted data never round-trips
through the LLM.
"""

import json

from google.adk.tools import ToolContext

from .. import routing


async def compute_routes(stops_json: str, tool_context: ToolContext) -> dict:
    """Compute the optimized driving route through a list of stops.

    Args:
        stops_json: JSON array of stops [{"lat", "lng", "label"}, ...]. The first
            element is the origin, the last is the destination, and everything in
            between is an intermediate stop whose visiting order may be optimized.

    Returns:
        dict with status, the optimized order of the intermediate stops, per-leg
        distance/duration summaries, and route totals.
    """
    # The authoritative stops come from session state, placed there by the
    # service — extra keys (parcelId, depot) must survive into the route, and
    # the LLM's re-serialization of stops_json can silently drop them.
    stops = tool_context.state.get("stops") or json.loads(stops_json)
    try:
        route = await routing.compute_route(
            stops,
            avoid_tolls=bool(tool_context.state.get("avoid_tolls")),
            vehicle_type=tool_context.state.get("vehicle_type", "car"))
    except routing.RoutingError as e:
        return {"status": "error", "message": str(e)}

    tool_context.state["routes_api_raw"] = route

    traffic_delay_min = round(
        (route["totalDurationSeconds"] - route["staticDurationSeconds"]) / 60)
    return {
        "status": "success",
        "ordered_stop_labels": [s["label"] for s in route["orderedStops"]],
        "legs": route["legs"],
        "total_distance_meters": route["totalDistanceMeters"],
        "total_duration_seconds": route["totalDurationSeconds"],
        "traffic_delay_minutes": max(0, traffic_delay_min),
        "estimated_toll_price": route["tollPrice"],
    }
