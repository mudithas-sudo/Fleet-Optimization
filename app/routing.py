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
        label = ordered_stops[li + 1]["label"] if li + 1 < len(ordered_stops) else "destination"
        steps.append({
            "maneuver": "ARRIVE",
            "instruction": f"Arrive at {label}",
            "endDist": round(cum),
        })
    return steps


# bikes get true two-wheeler routing; every other fleet type drives
TRAVEL_MODES = {"bike": "TWO_WHEELER"}


async def compute_route(stops: list[dict], avoid_tolls: bool = False,
                        optimize: bool = True, vehicle_type: str = "car") -> dict:
    """Compute a driving route through stops (first=origin, last=destination).

    optimize=True lets the Routes API reorder the intermediate stops;
    reroutes pass optimize=False to preserve the remaining stop order.
    """
    if len(stops) < 2:
        raise RoutingError("Need at least an origin and a destination.")

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
