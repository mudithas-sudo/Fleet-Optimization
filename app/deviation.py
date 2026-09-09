"""Rule-based deviation detection: distance from a point to the route polyline."""

import math
import os
import time
import uuid

EARTH_R = 6371000.0

THRESHOLD_M = float(os.environ.get("DEVIATION_THRESHOLD_M", "80"))
CONSECUTIVE = int(os.environ.get("DEVIATION_CONSECUTIVE", "3"))
# below this much movement between ticks the vehicle is treated as stopped —
# a parked vehicle can't be "taking a wrong turn", and its frozen position
# must not trip the progress-windowed matcher into a phantom deviation
STATIONARY_M = float(os.environ.get("STATIONARY_M", "6"))


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2)
    return 2 * EARTH_R * math.asin(math.sqrt(h))


def _to_xy(lat: float, lng: float, ref_lat: float) -> tuple[float, float]:
    # equirectangular projection to meters — fine at city scale
    x = math.radians(lng) * EARTH_R * math.cos(math.radians(ref_lat))
    y = math.radians(lat) * EARTH_R
    return x, y


def _project_on_segment(p, a, b) -> tuple[float, float]:
    """Distance from p to segment ab, and how far along ab the projection sits."""
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0:
        t = 0.0
    else:
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy), t * math.sqrt(seg_len_sq)


def project_to_route(lat: float, lng: float, path: list,
                     lo_m: float = 0.0, hi_m: float = float("inf")) -> tuple[float, float]:
    """(distance to the polyline, cumulative meters along it at the closest point).

    Only the [lo_m, hi_m] window of the route is considered. A route that
    doubles back can pass within meters of a genuinely off-route vehicle, so
    matching must stay near the vehicle's known progress, like real navigators.
    """
    ref_lat = lat
    p = _to_xy(lat, lng, ref_lat)
    best_dist, best_along = float("inf"), 0.0
    cum = 0.0
    prev = _to_xy(path[0][0], path[0][1], ref_lat)
    for i in range(1, len(path)):
        cur = _to_xy(path[i][0], path[i][1], ref_lat)
        seg_len = math.hypot(cur[0] - prev[0], cur[1] - prev[1])
        if cum + seg_len >= lo_m and cum <= hi_m + EDGE_SLACK_M:
            d, along = _project_on_segment(p, prev, cur)
            # the projected point must sit inside the window (with a little
            # slack past the leading edge, so barely outrunning the window
            # reads as small forward progress, not a huge lateral distance)
            if lo_m <= cum + along <= hi_m + EDGE_SLACK_M and d < best_dist:
                best_dist, best_along = d, cum + along
        cum += seg_len
        prev = cur
    if best_dist == float("inf"):  # window missed the route entirely
        return project_to_route(lat, lng, path)
    return best_dist, best_along


# how far the matching window extends behind/ahead of the known progress.
# Ahead is bounded by plausible movement between 1 s updates (<=120 km/h
# is ~35 m/tick) — a wide window lets nearby parallel streets of the same
# route (loops, one-way pairs) swallow a genuine deviation.
WINDOW_BACK_M = 300.0
WINDOW_AHEAD_M = 250.0
EDGE_SLACK_M = 120.0


def check_position(trip: dict, runtime: dict, lat: float, lng: float) -> tuple[dict | None, float]:
    """Update deviation state for a new position.

    Returns (alert-or-None, cumulative meters along the route at the closest
    on-route point) — the latter feeds auto-reroute.
    """
    prev_along = runtime.get("along", 0.0)

    prev_pos = runtime.get("last_pos")
    runtime["last_pos"] = (lat, lng)
    stationary = (prev_pos is not None
                  and _haversine_m(prev_pos, (lat, lng)) < STATIONARY_M)

    path = trip["route"]["path"]
    dist, along = project_to_route(
        lat, lng, path, prev_along - WINDOW_BACK_M, prev_along + WINDOW_AHEAD_M)

    if dist > THRESHOLD_M:
        # a poor windowed match can just mean our progress estimate drifted
        # ahead (loops / doubled-back routes). Re-check against the whole route;
        # if THAT lands on-route, trust it and re-sync `along` (even backward).
        full_dist, full_along = project_to_route(lat, lng, path)
        if full_dist <= THRESHOLD_M:
            dist, along = full_dist, full_along
            runtime["along"] = along

    if dist <= THRESHOLD_M:
        # progress only counts while on the route, and never moves backward:
        # where a route crosses or doubles back on itself, the projection can
        # tie with an EARLIER passage of the same spot — a vehicle drives
        # forward, so keep the furthest confirmed progress
        runtime["along"] = max(runtime.get("along", prev_along), along)

    if stationary:
        # a parked vehicle isn't taking a wrong turn — never escalate. If it
        # stopped back ON the route, clear any stale deviation state so the
        # stall check isn't blocked by a "deviating" flag that will never lift.
        runtime["consecutive"] = 0
        if dist <= THRESHOLD_M and runtime.get("alerting"):
            runtime["alerting"] = False
            return _alert(trip, "back_on_route", dist, lat, lng,
                          "Vehicle is back on the planned route"), along
        return None, along

    alert = None
    if dist > THRESHOLD_M:
        runtime["consecutive"] += 1
        if runtime["consecutive"] >= CONSECUTIVE and not runtime["alerting"]:
            runtime["alerting"] = True
            alert = _alert(trip, "deviation", dist, lat, lng,
                           f"Vehicle is {dist:.0f} m off the planned route")
    else:
        runtime["consecutive"] = 0
        if runtime["alerting"]:
            runtime["alerting"] = False
            alert = _alert(trip, "back_on_route", dist, lat, lng,
                           "Vehicle is back on the planned route")
    return alert, along


def _alert(trip, alert_type, dist, lat, lng, message) -> dict:
    return {
        "id": f"a-{uuid.uuid4().hex[:6]}",
        "tripId": trip["id"],
        "type": alert_type,
        "distanceMeters": round(dist, 1),
        "position": {"lat": lat, "lng": lng},
        "ts": time.time(),
        "message": message,
    }
