"""SQLite persistence: trips survive restarts, positions are logged per trip."""

import json
import math
import os
import sqlite3
import threading

DB_PATH = os.environ.get("DB_PATH", "fleet.db")

_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.execute("PRAGMA journal_mode=WAL")
_lock = threading.Lock()

_conn.executescript("""
CREATE TABLE IF NOT EXISTS trips (
  id TEXT PRIMARY KEY,
  created_at REAL,
  data TEXT
);
CREATE TABLE IF NOT EXISTS positions (
  trip_id TEXT,
  lat REAL,
  lng REAL,
  ts REAL
);
CREATE INDEX IF NOT EXISTS idx_positions_trip ON positions(trip_id);
CREATE INDEX IF NOT EXISTS idx_positions_trip_ts ON positions(trip_id, ts);
CREATE TABLE IF NOT EXISTS parcels  (id TEXT PRIMARY KEY, created_at REAL, data TEXT);
CREATE TABLE IF NOT EXISTS vehicles (id TEXT PRIMARY KEY, created_at REAL, data TEXT);
CREATE TABLE IF NOT EXISTS drivers  (id TEXT PRIMARY KEY, created_at REAL, data TEXT);
CREATE TABLE IF NOT EXISTS pods     (id TEXT PRIMARY KEY, created_at REAL, data TEXT);
CREATE TABLE IF NOT EXISTS notifications (id TEXT PRIMARY KEY, created_at REAL, data TEXT);
CREATE TABLE IF NOT EXISTS users    (id TEXT PRIMARY KEY, created_at REAL, data TEXT);
CREATE TABLE IF NOT EXISTS route_templates (id TEXT PRIMARY KEY, created_at REAL, data TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
""")

# ---- generic JSON-blob entities (parcels / vehicles / drivers) ----

_ENTITY_TABLES = {"parcels", "vehicles", "drivers", "pods", "notifications", "users", "route_templates"}


def _table(table: str) -> str:
    if table not in _ENTITY_TABLES:
        raise ValueError(f"unknown entity table: {table}")
    return table


def save_entity(table: str, obj: dict) -> None:
    with _lock:
        _conn.execute(
            f"INSERT INTO {_table(table)}(id, created_at, data) VALUES(?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (obj["id"], obj.get("createdAt", 0), json.dumps(obj)))
        _conn.commit()


def load_entity(table: str, entity_id: str) -> dict | None:
    with _lock:
        row = _conn.execute(
            f"SELECT data FROM {_table(table)} WHERE id=?", (entity_id,)).fetchone()
    return json.loads(row[0]) if row else None


def all_entities(table: str) -> list[dict]:
    with _lock:
        rows = _conn.execute(
            f"SELECT data FROM {_table(table)} ORDER BY created_at").fetchall()
    return [json.loads(r[0]) for r in rows]


def delete_entity(table: str, entity_id: str) -> None:
    with _lock:
        _conn.execute(f"DELETE FROM {_table(table)} WHERE id=?", (entity_id,))
        _conn.commit()


def get_setting(key: str):
    with _lock:
        row = _conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def set_setting(key: str, value) -> None:
    with _lock:
        _conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)))
        _conn.commit()


def save_trip(trip: dict) -> None:
    with _lock:
        _conn.execute(
            "INSERT INTO trips(id, created_at, data) VALUES(?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (trip["id"], trip.get("createdAt", 0), json.dumps(trip)))
        _conn.commit()


def load_trip(trip_id: str) -> dict | None:
    with _lock:
        row = _conn.execute("SELECT data FROM trips WHERE id=?", (trip_id,)).fetchone()
    return json.loads(row[0]) if row else None


def all_trips() -> list[dict]:
    with _lock:
        rows = _conn.execute(
            "SELECT data FROM trips ORDER BY created_at DESC").fetchall()
    return [json.loads(r[0]) for r in rows]


def delete_trip(trip_id: str) -> None:
    with _lock:
        _conn.execute("DELETE FROM trips WHERE id=?", (trip_id,))
        _conn.execute("DELETE FROM positions WHERE trip_id=?", (trip_id,))
        _conn.commit()


def clear_trips() -> int:
    with _lock:
        n = _conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
        _conn.execute("DELETE FROM trips")
        _conn.execute("DELETE FROM positions")
        _conn.commit()
    return n


def clear_positions(trip_id: str) -> None:
    with _lock:
        _conn.execute("DELETE FROM positions WHERE trip_id=?", (trip_id,))
        _conn.commit()


def add_position(trip_id: str, lat: float, lng: float, ts: float) -> None:
    with _lock:
        _conn.execute("INSERT INTO positions VALUES(?, ?, ?, ?)",
                      (trip_id, lat, lng, ts))
        _conn.commit()


def position_at(trip_id: str, ts: float) -> dict | None:
    """The last logged position at or before `ts`."""
    with _lock:
        row = _conn.execute(
            "SELECT lat, lng, ts FROM positions WHERE trip_id=? AND ts<=? ORDER BY ts DESC LIMIT 1",
            (trip_id, ts)).fetchone()
    return {"lat": row[0], "lng": row[1], "ts": row[2]} if row else None


def positions_until(trip_id: str, ts: float | None) -> list[tuple]:
    with _lock:
        if ts is None:
            return _conn.execute("SELECT lat, lng FROM positions WHERE trip_id=? ORDER BY ts", (trip_id,)).fetchall()
        return _conn.execute("SELECT lat, lng FROM positions WHERE trip_id=? AND ts<=? ORDER BY ts",
                             (trip_id, ts)).fetchall()


def actual_distance_m(trip_id: str) -> float:
    """Distance actually driven, from the logged positions."""
    with _lock:
        rows = _conn.execute(
            "SELECT lat, lng FROM positions WHERE trip_id=? ORDER BY ts",
            (trip_id,)).fetchall()
    total = 0.0
    for (lat1, lng1), (lat2, lng2) in zip(rows, rows[1:]):
        p1, p2 = map(math.radians, [lat1, lat2])
        dlat, dlng = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
        h = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
        total += 2 * 6371000 * math.asin(math.sqrt(h))
    return total
