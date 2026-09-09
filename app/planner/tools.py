"""compute_routes ADK function tool — wraps app.routing.compute_route.

The tool returns a SMALL dict to the model (order, legs, totals) and stashes
the full route (polylines, decoded path, steps) in
tool_context.state["routes_api_raw"] so trusted data never round-trips
through the LLM.
"""

import json
import time

from google.adk.tools import ToolContext

from .. import routing


def _stop_key(s: dict) -> str:
    return f"{s.get('parcelId')}:{s.get('kind')}"


def _apply_order(stops: list[dict], order: list) -> list[dict]:
    """Reorder the authoritative stops (depot first) by the model's proposed
    order of "<parcelId>:<kind>" keys, appending anything it left out, then
    repairing precedence."""
    depot, rest = stops[0], stops[1:]
    by_key = {_stop_key(s): s for s in rest}
    seen, seq = set(), []
    for key in order:
        s = by_key.get(str(key).strip())
        if s and id(s) not in seen:
            seq.append(s)
            seen.add(id(s))
    for s in rest:                      # anything the model omitted
        if id(s) not in seen:
            seq.append(s)
    return [depot] + routing.repair_precedence(seq)


async def compute_routes(stops_json: str, tool_context: ToolContext,
                         order_json: str = "") -> dict:
    """Compute the driving route through the run's stops.

    Args:
        stops_json: the stops as JSON (informational — the authoritative list
            comes from session state so parcel/kind metadata can't be dropped).
        order_json: REQUIRED when the stops include pickups. A JSON array of
            "<parcelId>:<kind>" strings (kind is "pickup" or "delivery"), depot
            excluded, listing every non-depot stop once, in your chosen visiting
            order. A parcel's pickup must come before its delivery, and each
            pickup should land within its [earliest, latest] window if given.
            Pass "" (empty) when there are no pickups — the route is optimised
            automatically then.

    Returns:
        dict with status, the final stop order, per-stop ETA + at-risk flags,
        per-leg summaries and route totals.
    """
    stops = tool_context.state.get("stops") or json.loads(stops_json)
    has_pickup = any(s.get("kind") == "pickup" for s in stops)

    route_stops, presequenced = stops, False
    if has_pickup and order_json.strip():
        try:
            order = json.loads(order_json)
            if isinstance(order, list) and order:
                route_stops = _apply_order(stops, order)
                presequenced = True
        except (ValueError, TypeError):
            pass   # fall back to the deterministic heuristic below

    try:
        route = await routing.compute_route(
            route_stops,
            avoid_tolls=bool(tool_context.state.get("avoid_tolls")),
            vehicle_type=tool_context.state.get("vehicle_type", "car"),
            presequenced=presequenced)
    except routing.RoutingError as e:
        return {"status": "error", "message": str(e)}

    tool_context.state["routes_api_raw"] = route

    # per-stop ETA from now + window/deadline risk, so the briefing can flag it
    now, elapsed, stop_rows = time.time(), 0.0, []
    for i, s in enumerate(route["orderedStops"]):
        if i > 0 and i - 1 < len(route["legs"]):
            elapsed += route["legs"][i - 1]["durationSeconds"]
        if not s.get("parcelId"):
            continue
        eta = now + elapsed
        latest, earliest, deadline = s.get("latest"), s.get("earliest"), s.get("deadline")
        at_risk = ((latest is not None and eta > latest)
                   or (earliest is not None and eta < earliest)
                   or (deadline is not None and eta > deadline))
        stop_rows.append({
            "parcel": s.get("parcelName") or s["label"],
            "place": s.get("place") or s["label"],
            "kind": s.get("kind"),
            "etaInMinutes": round(elapsed / 60),
            "atRisk": at_risk,
        })

    traffic_delay_min = round(
        (route["totalDurationSeconds"] - route["staticDurationSeconds"]) / 60)
    return {
        "status": "success",
        "ordered_stops": [
            ("Start at " + s["label"]) if i == 0 else
            ("Collect " + (s.get("parcelName") or s["label"]) + " at " + (s.get("place") or s["label"]))
            if s.get("kind") == "pickup" else
            ("Drop off " + (s.get("parcelName") or s["label"]) + " at " + (s.get("place") or s["label"]))
            for i, s in enumerate(route["orderedStops"])
        ],
        "stops": stop_rows,
        "legs": route["legs"],
        "total_distance_meters": route["totalDistanceMeters"],
        "total_duration_seconds": route["totalDurationSeconds"],
        "traffic_delay_minutes": max(0, traffic_delay_min),
        "estimated_toll_price": route["tollPrice"],
    }
