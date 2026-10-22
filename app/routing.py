"""Route computation against Google Routes API v2 (with FAKE_ROUTES fallback).

Shared by the ADK planner tool and the auto-reroute path, so both produce the
same route dict shape:
  { orderedStops, polyline, path, legs, steps, totalDistanceMeters,
    totalDurationSeconds }
"""

import math
import os

import httpx

from . import polyline

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
FIELD_MASK = ",".join([
    "routes.optimizedIntermediateWaypointIndex",
    "routes.distanceMeters",
    "routes.duration",
    "routes.staticDuration",
    "routes.polyline.encodedPolyline",
    "routes.travelAdvisory.tollInfo",
    "routes.travelAdvisory.speedReadingIntervals",
    "routes.legs.distanceMeters",
    "routes.legs.duration",
    "routes.legs.polyline.encodedPolyline",
    "routes.legs.steps.distanceMeters",
    "routes.legs.steps.navigationInstruction",
])


class RoutingError(Exception):
    pass


def _latlng(stop: dict) -> dict:
    return {"location": {"latLng": {"latitude": stop["lat"], "longitude": stop["lng"]}}}


def _haversine_m(a: dict, b: dict) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, [a["lat"], a["lng"], b["lat"], b["lng"]])
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(h))


def _fake_route(origin: dict, dest: dict, intermediates: list[dict]) -> dict:
    """Straight-line stand-in for the Routes API while billing is not enabled."""
    ordered = [origin, *intermediates, dest]
    legs = []
    full_path: list[tuple[float, float]] = []
    for a, b in zip(ordered, ordered[1:]):
        dist = _haversine_m(a, b) * 1.3  # rough road factor
        steps = 20
        leg_path = [
            (a["lat"] + (b["lat"] - a["lat"]) * i / steps,
             a["lng"] + (b["lng"] - a["lng"]) * i / steps)
            for i in range(steps + 1)
        ]
        full_path.extend(leg_path if not full_path else leg_path[1:])
        legs.append({
            "distanceMeters": round(dist),
            "duration": f"{round(dist / 11)}s",  # ~40 km/h
            "polyline": {"encodedPolyline": polyline.encode(leg_path)},
            "steps": [{
                "distanceMeters": round(dist),
                "navigationInstruction": {
                    "maneuver": "DEPART",
                    "instructions": f"Head toward {b['label']}",
                },
            }],
        })
    return {
        "optimizedIntermediateWaypointIndex": list(range(len(intermediates))),
        "distanceMeters": sum(l["distanceMeters"] for l in legs),
        "duration": f"{sum(int(l['duration'][:-1]) for l in legs)}s",
        "polyline": {"encodedPolyline": polyline.encode(full_path)},
        "legs": legs,
    }


def _flatten_steps(legs: list[dict], ordered_stops: list[dict]) -> list[dict]:
    """Turn-by-turn steps across the whole route, with cumulative end distance,
    plus an ARRIVE pseudo-step at each stop so the driver app can announce it."""
    steps = []
    cum = 0.0
    for li, leg in enumerate(legs):
        # a ~0 m leg is two coincident stops (e.g. parcels collected at one
        # warehouse) — it has no real driving, so skip its turn-by-turn steps
        if leg.get("distanceMeters", 0) > 5:
            for st in leg.get("steps", []):
                dist = st.get("distanceMeters", 0)
                cum += dist
                nav = st.get("navigationInstruction", {})
                instruction = nav.get("instructions", "").split("\n")[0]
                if not instruction:
                    continue
                steps.append({
                    "maneuver": nav.get("maneuver", "STRAIGHT"),
                    "instruction": instruction,
                    "endDist": round(cum),
                })
        stop = ordered_stops[li + 1] if li + 1 < len(ordered_stops) else {}
        label = stop.get("label", "destination")
        kind = stop.get("kind")
        verb = "Collect at" if kind == "pickup" else "Deliver to" if kind == "delivery" else "Arrive at"
        instruction = f"{verb} {label}"
        # coincident stops (e.g. several parcels collected at one warehouse)
        # produce a 0 m leg and a repeat announcement — drop the duplicate
        if (steps and steps[-1]["maneuver"] == "ARRIVE"
                and steps[-1]["instruction"] == instruction):
            continue
        steps.append({
            "maneuver": "ARRIVE",
            "instruction": instruction,
            "endDist": round(cum),
        })
    return steps


def hub_last(stops: list[dict]) -> list[dict]:
    """Pickup requests are brought back to the hub: those drop-off stops always
    close the run (stable otherwise). A no-op when the run has none."""
    if not any(s.get("hubReturn") for s in stops):
        return stops
    return [s for s in stops if not s.get("hubReturn")] + [s for s in stops if s.get("hubReturn")]


def _order_with_precedence(stops: list[dict]) -> list[dict]:
    """Nearest-neighbour order from the depot with one hard rule: a parcel's
    delivery stop is never visited before its pickup stop. Used instead of the
    Routes API's waypoint optimisation, which has no notion of precedence."""
    depot, rest, ordered = stops[0], list(stops[1:]), []
    cur = depot
    while rest:
        blocked = {s["parcelId"] for s in rest if s.get("kind") == "pickup"}
        cands = [s for s in rest
                 if not (s.get("kind") == "delivery" and s.get("parcelId") in blocked)]
        nxt = min(cands, key=lambda s: _haversine_m(cur, s))
        ordered.append(nxt)
        rest.remove(nxt)
        cur = nxt
    return [depot] + hub_last(ordered)


PRIORITY_RANK = {"express": 0, "priority": 1, "standard": 2}


def has_special_handling(stops: list[dict]) -> bool:
    """Any express/priority parcel or delivery window on the run (depot excluded)?"""
    return any((s.get("priority") or "standard") != "standard" or s.get("windowStart")
               or s.get("hubReturn") or s.get("requestType")
               for s in stops[1:])


def sequence_stops(stops: list[dict], now: float | None = None) -> list[dict]:
    """Visiting order for runs with express/priority parcels or delivery
    windows: express first, then priority, then standard; within a tier the
    nearest stop next. A stop whose delivery window opens more than 45 minutes
    from now waits behind the stops that are ready. A parcel's delivery is never
    ahead of its pickup. Deterministic — no Routes optimisation or LLM involved,
    so the dispatcher's priorities are always honoured."""
    import time as _time
    now = now or _time.time()
    depot, rest, ordered, cur = stops[0], list(stops[1:]), [], stops[0]
    while rest:
        blocked = {s["parcelId"] for s in rest if s.get("kind") == "pickup"}
        cands = [s for s in rest
                 if not (s.get("kind") == "delivery" and s.get("parcelId") in blocked)]

        def key(s):
            rank = PRIORITY_RANK.get(s.get("priority") or "standard", 2)
            # a delivery window not yet open, or a pickup request before the customer's cut-off
            # (unless the customer confirmed the shipment can go early), waits behind ready stops
            opens = s.get("earliest") if s.get("kind") == "pickup" else s.get("windowStart")
            not_ready = bool(opens and opens > now + 45 * 60
                             and (s.get("kind") != "pickup" or s.get("requestType")))
            return (rank + (3 if not_ready else 0), _haversine_m(cur, s))

        nxt = min(cands, key=key)
        ordered.append(nxt)
        rest.remove(nxt)
        cur = nxt
    return [depot] + hub_last(ordered)


def repair_precedence(rest: list[dict]) -> list[dict]:
    """Take a proposed visiting order (depot excluded) and make it
    precedence-valid: a delivery whose pickup hasn't been visited yet is held
    aside and re-inserted right after its pickup. A stable no-op when the order
    is already valid. The planner agent proposes an order; this is the guard."""
    has_pickup = {s["parcelId"] for s in rest if s.get("kind") == "pickup"}
    result, held, collected = [], {}, set()
    for s in rest:
        pid = s.get("parcelId")
        if (s.get("kind") == "delivery" and pid in has_pickup and pid not in collected):
            held[pid] = s
            continue
        result.append(s)
        if s.get("kind") == "pickup":
            collected.add(pid)
            if pid in held:
                result.append(held.pop(pid))
    result.extend(held.values())   # pickup never appeared (shouldn't happen)
    return result


# a delivery run lives in one metro area; a stop hundreds of km away is a bad address or a bad GPS fix
MAX_SPAN_KM = float(os.environ.get("MAX_RUN_SPAN_KM", "300"))


def check_span(stops: list[dict]) -> None:
    """Refuse a route whose stops are absurdly far from its start (e.g. an address that geocoded
    to another country, or a vehicle position that is not where the vehicle is)."""
    if len(stops) < 2:
        return
    far = max(stops[1:], key=lambda s: _haversine_m(stops[0], s))
    km = _haversine_m(stops[0], far) / 1000
    if km > MAX_SPAN_KM:
        raise RoutingError(
            f"“{far.get('label') or 'A stop'}” is {km:,.0f} km from the start of the run (limit {MAX_SPAN_KM:g} km). "
            "Check that address, or the vehicle's last position.")


# bikes get true two-wheeler routing; every other fleet type drives
TRAVEL_MODES = {"bike": "TWO_WHEELER"}


async def compute_route(stops: list[dict], avoid_tolls: bool = False,
                        optimize: bool = True, vehicle_type: str = "car",
                        presequenced: bool = False) -> dict:
    """Compute a driving route through stops (first=origin, last=destination).

    optimize=True lets the Routes API reorder the intermediate stops;
    reroutes pass optimize=False to preserve the remaining stop order.

    When any stop is a pickup, the Routes optimiser can't be used (no
    precedence support). `presequenced=True` means the caller has already put
    the stops in a valid visiting order (the planner agent + repair_precedence);
    otherwise the deterministic nearest-neighbour heuristic is applied here.
    """
    if len(stops) < 2:
        raise RoutingError("Need at least an origin and a destination.")
    check_span(stops)

    stops = hub_last(stops)
    if any(s.get("kind") == "pickup" for s in stops):
        if not presequenced:
            stops = _order_with_precedence(stops)
        optimize = False

    origin, dest, intermediates = stops[0], stops[-1], stops[1:-1]

    if os.environ.get("FAKE_ROUTES") == "1":
        route = _fake_route(origin, dest, intermediates)
    else:
        body = {
            "origin": _latlng(origin),
            "destination": _latlng(dest),
            "travelMode": TRAVEL_MODES.get(vehicle_type, "DRIVE"),
            "routingPreference": os.environ.get("ROUTING_PREF", "TRAFFIC_AWARE"),
            "extraComputations": ["TOLLS", "TRAFFIC_ON_POLYLINE"],
            "languageCode": os.environ.get("ROUTE_LANG", "en"),
        }
        if intermediates:
            body["intermediates"] = [_latlng(s) for s in intermediates]
        if optimize and len(intermediates) >= 2:
            body["optimizeWaypointOrder"] = True
        if avoid_tolls:
            body["routeModifiers"] = {"avoidTolls": True}
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                ROUTES_URL,
                json=body,
                headers={
                    "X-Goog-Api-Key": os.environ.get("MAPS_SERVER_KEY") or os.environ["MAPS_API_KEY"],
                    "X-Goog-FieldMask": FIELD_MASK,
                },
            )
        if resp.status_code != 200:
            raise RoutingError(
                f"Routes API HTTP {resp.status_code}: {resp.text[:300]}")
        route = resp.json()["routes"][0]

    optimized_order = route.get("optimizedIntermediateWaypointIndex",
                                list(range(len(intermediates))))
    ordered_stops = [origin] + [intermediates[i] for i in optimized_order] + [dest]

    legs_summary = []
    for i, leg in enumerate(route.get("legs", [])):
        legs_summary.append({
            "from": ordered_stops[i]["label"],
            "to": ordered_stops[i + 1]["label"],
            "distanceMeters": leg["distanceMeters"],
            "durationSeconds": int(leg["duration"].rstrip("s")),
        })

    total_distance = route.get("distanceMeters", sum(l["distanceMeters"] for l in legs_summary))
    total_duration = int(route.get("duration", "0s").rstrip("s")) or sum(
        l["durationSeconds"] for l in legs_summary)
    static_duration = int(route.get("staticDuration", "0s").rstrip("s")) or total_duration

    advisory = route.get("travelAdvisory", {})
    # congested stretches, as [startIdx, endIdx, "SLOW"|"TRAFFIC_JAM"] over the
    # overview polyline (route-level interval indices map onto it directly)
    traffic = [
        [i.get("startPolylinePointIndex", 0), i["endPolylinePointIndex"], i["speed"]]
        for i in advisory.get("speedReadingIntervals", [])
        if i.get("speed") in ("SLOW", "TRAFFIC_JAM") and "endPolylinePointIndex" in i
    ]
    toll_price = None
    for price in advisory.get("tollInfo", {}).get("estimatedPrice", []):
        amount = int(price.get("units", 0)) + price.get("nanos", 0) / 1e9
        toll_price = f"{price.get('currencyCode', '')} {amount:,.0f}".strip()
        break

    encoded = route["polyline"]["encodedPolyline"]
    return {
        "orderedStops": ordered_stops,
        "polyline": encoded,
        "path": [list(p) for p in polyline.decode(encoded)],
        "legs": legs_summary,
        "steps": _flatten_steps(route.get("legs", []), ordered_stops),
        "traffic": traffic,
        "tollPrice": toll_price,
        "totalDistanceMeters": total_distance,
        "totalDurationSeconds": total_duration,
        "staticDurationSeconds": static_duration,
    }
