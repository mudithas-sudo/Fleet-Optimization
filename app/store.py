"""Trip store: SQLite-backed trips, in-memory runtime state (deviation
counters, reroute guards) and SSE subscriber queues (per-trip + global)."""

import asyncio
import json
import time
import uuid

from . import db

# runtime-only state, keyed by trip id — rebuilt empty after a restart
_RUNTIME: dict[str, dict] = {}

_TRIP_SUBSCRIBERS: dict[str, list[asyncio.Queue]] = {}
_GLOBAL_SUBSCRIBERS: list[asyncio.Queue] = []


def new_trip(stops: list[dict], route: dict, avoid_tolls: bool = False,
             vehicle_type: str = "car") -> dict:
    trip = {
        "id": f"t-{uuid.uuid4().hex[:6]}",
        "status": "planned",
        "createdAt": time.time(),
        "startedAt": None,
        "endedAt": None,
        "avoidTolls": avoid_tolls,
        "vehicleType": vehicle_type,
        "routeVersion": 1,
        "stops": stops,
        "route": route,
        "lastPosition": None,
        "alerts": [],
    }
    db.save_trip(trip)
    return trip


def get_trip(trip_id: str) -> dict | None:
    return db.load_trip(trip_id)


def save_trip(trip: dict) -> None:
    db.save_trip(trip)


def list_trips() -> list[dict]:
    return db.all_trips()


def delete_trip(trip_id: str) -> None:
    db.delete_trip(trip_id)
    _RUNTIME.pop(trip_id, None)


def clear_trips() -> int:
    n = db.clear_trips()
    _RUNTIME.clear()
    return n


def reset_trip_runtime(trip_id: str) -> None:
    db.clear_positions(trip_id)
    _RUNTIME.pop(trip_id, None)


def runtime(trip_id: str) -> dict:
    return _RUNTIME.setdefault(trip_id, {
        "consecutive": 0, "alerting": False, "along": 0.0,
        "last_reroute": 0.0, "rerouting": False, "last_pos": None,
        # pickup-window risk + "vehicle not moving" detection
        "pickup_risk_fired": set(), "stall_along": 0.0,
        "stall_since": 0.0, "stall_fired": False, "stall_logged": 0.0,
        "stall_blocked": None, "driver_halted": False,
    })


def log_position(trip_id: str, lat: float, lng: float, ts: float) -> None:
    db.add_position(trip_id, lat, lng, ts)


def actual_distance_m(trip_id: str) -> float:
    return db.actual_distance_m(trip_id)


def subscribe(trip_id: str | None = None) -> asyncio.Queue:
    """Subscribe to one trip's events, or to all events when trip_id is None."""
    q: asyncio.Queue = asyncio.Queue()
    if trip_id is None:
        _GLOBAL_SUBSCRIBERS.append(q)
    else:
        _TRIP_SUBSCRIBERS.setdefault(trip_id, []).append(q)
    return q


def unsubscribe(trip_id: str | None, q: asyncio.Queue) -> None:
    subs = _GLOBAL_SUBSCRIBERS if trip_id is None else _TRIP_SUBSCRIBERS.get(trip_id, [])
    if q in subs:
        subs.remove(q)


def broadcast(trip_id: str, event: str, data: dict) -> None:
    payload = {"event": event, "data": json.dumps({**data, "tripId": trip_id})}
    for q in _TRIP_SUBSCRIBERS.get(trip_id, []) + _GLOBAL_SUBSCRIBERS:
        q.put_nowait(payload)


def broadcast_global(event: str, data: dict) -> None:
    """Events not tied to a trip (e.g. parcel mutations) — global stream only."""
    payload = {"event": event, "data": json.dumps(data)}
    for q in _GLOBAL_SUBSCRIBERS:
        q.put_nowait(payload)
