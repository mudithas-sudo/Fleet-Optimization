"""Deterministic checks for users, roles, sessions and driver keys — no keys
(FAKE_ROUTES). Run: `venv/bin/python tests/test_auth.py` (or pytest)."""

import asyncio
import os
import sys
import tempfile
import time

os.environ.setdefault("FAKE_ROUTES", "1")
os.environ.setdefault("GOOGLE_API_KEY", "unused")
os.environ.setdefault("MAPS_API_KEY", "unused")
os.environ["AUTH_ENABLED"] = "0"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient     # noqa: E402

from app import auth, db, delivery, main, routing, store  # noqa: E402

DEPOT = {"lat": 6.93, "lng": 79.84, "label": "Hub"}
delivery.get_depot = lambda: DEPOT
PW = "correct-horse-9"


def _client(username=None, password=PW) -> TestClient:
    c = TestClient(main.app)
    if username:
        r = c.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
    return c


def _user(username, role):
    return auth.create_user(username, username.title(), role, PW)


def _parcel(name, i=1):
    return delivery.new_parcel({"name": name, "size": "small", "deadline": time.time() + 7200,
                                "destination": {"lat": 6.9 + i * 0.01, "lng": 79.85, "label": f"{name} addr"}})


def _live_run(parcels, driver):
    stops = delivery.build_run_stops(parcels)
    route = asyncio.run(routing.compute_route(stops, vehicle_type="van"))
    trip = store.new_trip(stops, route, vehicle_type="van")
    trip.update({"driverId": driver["id"], "vehicleId": driver["vehicleId"], "status": "active",
                 "parcelIds": [p["id"] for p in parcels], "plannedEtaPerStop": []})
    store.save_trip(trip)
    for p in parcels:
        delivery.set_parcel_status(p, "assigned", trip_id=trip["id"])
        delivery.set_parcel_status(p, "in_transit", trip_id=trip["id"])
    return trip


# ---- fixtures: the app starts with auth off; users are created directly
os.environ["AUTH_ENABLED"] = "0"
OPEN = _client()
van = delivery.new_vehicle({"name": "Van", "type": "van"})
DRV_A = delivery.new_driver({"name": "Ann", "vehicleId": van["id"]})
DRV_B = delivery.new_driver({"name": "Bob", "vehicleId": van["id"]})
P_A = _parcel("Mine", 1)
TRIP_A = _live_run([P_A], DRV_A)
for r in ("admin", "supervisor", "dispatcher", "viewer"):
    _user(f"{r}1", r)


def test_auth_is_off_by_default():
    os.environ["AUTH_ENABLED"] = "0"
    me = OPEN.get("/api/auth/me").json()
    assert me["authEnabled"] is False and me["user"]["role"] == "admin"
    assert OPEN.get("/api/parcels").status_code == 200


def test_passwords_are_hashed_and_verified():
    h = auth.hash_password(PW)
    assert PW not in h and h.startswith("scrypt$") and auth.hash_password(PW) != h
    assert auth.verify_password(PW, h) and not auth.verify_password("nope", h)
    assert not auth.verify_password(PW, "garbage")
    stored = db.all_entities("users")[0]
    assert "password" not in stored and stored["passwordHash"].startswith("scrypt$")


def test_everything_needs_a_login_except_the_public_bits():
    os.environ["AUTH_ENABLED"] = "1"
    try:
        c = _client()
        assert c.get("/api/parcels").status_code == 401
        assert c.get("/api/stream").status_code == 401
        assert c.post("/api/parcels", json={}).status_code == 401
        assert c.get("/api/auth/me").status_code == 401
        assert c.get("/api/track/FO-NOPE0000").status_code == 404          # public, just not found
        assert c.get("/api/config").status_code == 200
        assert c.get("/static/login.html").status_code == 200
        assert c.get("/static/track.html").status_code == 200
    finally:
        os.environ["AUTH_ENABLED"] = "0"


def test_login_logout_and_bad_credentials():
    os.environ["AUTH_ENABLED"] = "1"
    try:
        c = _client()
        assert c.post("/api/auth/login", json={"username": "viewer1", "password": "wrong"}).status_code == 401
        assert c.post("/api/auth/login", json={"username": "ghost", "password": PW}).status_code == 401
        r = c.post("/api/auth/login", json={"username": "VIEWER1", "password": PW})      # case-insensitive
        assert r.status_code == 200 and r.json()["user"]["role"] == "viewer"
        assert "httponly" in r.headers["set-cookie"].lower() and "samesite=lax" in r.headers["set-cookie"].lower()
        assert c.get("/api/parcels").status_code == 200
        c.post("/api/auth/logout")
        assert c.get("/api/parcels").status_code == 401
    finally:
        os.environ["AUTH_ENABLED"] = "0"


def test_role_permissions():
    os.environ["AUTH_ENABLED"] = "1"
    try:
        viewer, disp, sup, adm = (_client(f"{r}1") for r in ("viewer", "dispatcher", "supervisor", "admin"))
        body = {"name": "X", "size": "small", "deadline": time.time() + 3600,
                "destination": {"lat": 6.9, "lng": 79.8, "label": "Somewhere"}}
        # viewer: read only
        assert viewer.get("/api/parcels").status_code == 200
        assert viewer.post("/api/parcels", json=body).status_code == 403
        assert viewer.get("/api/reports/summary").status_code == 403
        # dispatcher: deliveries yes, registry/reports no
        assert disp.post("/api/parcels", json=body).status_code == 200
        assert disp.post("/api/vehicles", json={"name": "V", "type": "car"}).status_code == 403
        assert disp.put("/api/settings/depot", json=DEPOT).status_code == 403
        assert disp.get("/api/reports/summary").status_code == 403
        assert disp.get("/api/users").status_code == 403
        assert disp.delete("/api/trips").status_code == 403
        # supervisor: registry + reports, but not users
        assert sup.post("/api/vehicles", json={"name": "V", "type": "car"}).status_code == 200
        assert sup.get("/api/reports/summary").status_code == 200
        assert sup.get("/api/reports/export?kind=parcels").status_code == 200
        assert sup.get("/api/users").status_code == 403
        # admin: everything
        assert adm.get("/api/users").status_code == 200
        assert adm.get("/api/reports/summary").status_code == 200
        # driver keys are only shown to staff who can use them
        assert all("token" not in d for d in viewer.get("/api/drivers").json())
        assert all(d.get("token") for d in disp.get("/api/drivers").json())
    finally:
        os.environ["AUTH_ENABLED"] = "0"


def test_user_management_rules():
    os.environ["AUTH_ENABLED"] = "1"
    try:
        adm = _client("admin1")
        me_id = adm.get("/api/auth/me").json()["user"]["id"]
        made = adm.post("/api/users", json={"username": "newbie", "name": "New B", "role": "dispatcher", "password": PW})
        assert made.status_code == 200 and "passwordHash" not in made.text
        assert adm.post("/api/users", json={"username": "newbie", "role": "viewer", "password": PW}).status_code == 409
        assert adm.post("/api/users", json={"username": "x", "role": "viewer", "password": PW}).status_code == 400
        assert adm.post("/api/users", json={"username": "shorty", "role": "viewer", "password": "abc"}).status_code == 400
        assert adm.post("/api/users", json={"username": "godmode", "role": "root", "password": PW}).status_code == 400
        nid = made.json()["id"]
        assert adm.put(f"/api/users/{nid}", json={"role": "supervisor"}).json()["role"] == "supervisor"
        # the only admin can't be demoted, switched off, or deleted — nor can you delete yourself
        assert adm.put(f"/api/users/{me_id}", json={"role": "viewer"}).status_code == 409
        assert adm.put(f"/api/users/{me_id}", json={"active": False}).status_code == 409
        assert adm.delete(f"/api/users/{me_id}").status_code == 409
        assert adm.delete(f"/api/users/{nid}").status_code == 200
        assert adm.put("/api/users/nope", json={"name": "x"}).status_code == 404
    finally:
        os.environ["AUTH_ENABLED"] = "0"


def test_switching_off_or_resetting_a_user_ends_their_session():
    os.environ["AUTH_ENABLED"] = "1"
    try:
        adm = _client("admin1")
        u = _user("temp1", "dispatcher")
        sess = _client("temp1")
        assert sess.get("/api/parcels").status_code == 200
        adm.put(f"/api/users/{u['id']}", json={"active": False})
        assert sess.get("/api/parcels").status_code == 401
        assert sess.post("/api/auth/login", json={"username": "temp1", "password": PW}).status_code == 401
        adm.put(f"/api/users/{u['id']}", json={"active": True})
        sess2 = _client("temp1")
        adm.put(f"/api/users/{u['id']}", json={"password": "a-brand-new-pass"})
        assert sess2.get("/api/parcels").status_code == 401, "a password reset signs everyone out"
        assert _client("temp1", "a-brand-new-pass").get("/api/parcels").status_code == 200
    finally:
        os.environ["AUTH_ENABLED"] = "0"


def test_change_own_password():
    os.environ["AUTH_ENABLED"] = "1"
    try:
        _user("pwuser", "viewer")
        a, b = _client("pwuser"), _client("pwuser")
        assert a.post("/api/auth/password", json={"current": "wrong", "new": "another-pass-1"}).status_code == 400
        assert a.post("/api/auth/password", json={"current": PW, "new": "short"}).status_code == 400
        assert a.post("/api/auth/password", json={"current": PW, "new": "another-pass-1"}).status_code == 200
        assert a.get("/api/parcels").status_code == 200, "this session stays signed in"
        assert b.get("/api/parcels").status_code == 401, "other sessions are signed out"
    finally:
        os.environ["AUTH_ENABLED"] = "0"


def test_tampered_or_expired_sessions_are_rejected():
    os.environ["AUTH_ENABLED"] = "1"
    try:
        c = _client("viewer1")
        good = c.cookies.get(auth.COOKIE)
        body, sig = good.rsplit(".", 1)
        c.cookies.set(auth.COOKIE, body + "." + ("0" * len(sig)))
        assert c.get("/api/parcels").status_code == 401
        import base64, json
        forged = base64.urlsafe_b64encode(json.dumps({"u": "u-whatever", "v": 0, "exp": time.time() + 999}).encode()).decode()
        c.cookies.set(auth.COOKIE, forged + "." + sig)
        assert c.get("/api/parcels").status_code == 401
        assert auth.user_from_token(None) is None and auth.user_from_token("nodot") is None
    finally:
        os.environ["AUTH_ENABLED"] = "0"


def test_repeated_wrong_passwords_lock_the_account_briefly():
    os.environ["AUTH_ENABLED"] = "1"
    auth._failures.clear()
    try:
        c = _client()
        codes = [c.post("/api/auth/login", json={"username": "viewer1", "password": f"bad{i}"}).status_code for i in range(7)]
        assert codes[:5] == [401] * 5 and codes[5:] == [429, 429]
        assert c.post("/api/auth/login", json={"username": "viewer1", "password": PW}).status_code == 429
    finally:
        auth._failures.clear()
        os.environ["AUTH_ENABLED"] = "0"


def test_driver_keys_protect_the_driver_endpoints():
    os.environ["AUTH_ENABLED"] = "1"
    try:
        c = _client()
        key_a, key_b = auth.driver_key(DRV_A), auth.driver_key(DRV_B)
        assert key_a != key_b
        q = f"/api/drivers/{DRV_A['id']}/queue"
        assert c.get(q).status_code == 401
        assert c.get(q + "?k=nonsense").status_code == 401
        assert c.get(q + f"?k={key_b}").status_code == 401, "another driver's key doesn't work"
        assert c.get(q + f"?k={key_a}").status_code == 200
        trip = f"/api/trips/{TRIP_A['id']}"
        assert c.get(trip).status_code == 401 and c.get(trip, headers={"X-Driver-Key": key_a}).status_code == 200
        assert c.get(trip + "/parcels", headers={"X-Driver-Key": key_a}).status_code == 200
        pos = {"lat": 6.9, "lng": 79.85}
        assert c.post(trip + "/position", json=pos, headers={"X-Driver-Key": key_b}).status_code == 401
        assert c.post(trip + "/position", json=pos, headers={"X-Driver-Key": key_a}).status_code == 200
        # proof of delivery needs the key of the driver who has the parcel
        pod = {"method": "handed_over", "receivedBy": "Mrs Silva"}
        assert c.post(f"/api/parcels/{P_A['id']}/pod", json=pod).status_code == 401
        assert c.post(f"/api/parcels/{P_A['id']}/pod", json=pod, headers={"X-Driver-Key": key_b}).status_code == 401
        assert c.post(f"/api/parcels/{P_A['id']}/pod", json=pod, headers={"X-Driver-Key": key_a}).status_code == 200
        # a driver key can't reach admin endpoints
        assert c.get("/api/parcels", headers={"X-Driver-Key": key_a}).status_code == 401
        assert c.get("/api/reports/summary", headers={"X-Driver-Key": key_a}).status_code == 401
    finally:
        os.environ["AUTH_ENABLED"] = "0"


def test_first_admin_is_created_once():
    for u in db.all_entities("users"):
        db.delete_entity("users", u["id"])
    os.environ["AUTH_ENABLED"] = "1"
    os.environ["ADMIN_PASSWORD"] = "bootstrap-pass-1"
    try:
        auth.bootstrap_admin()
        users = db.all_entities("users")
        assert len(users) == 1 and users[0]["role"] == "admin" and users[0]["username"] == "admin"
        auth.bootstrap_admin()
        assert len(db.all_entities("users")) == 1
        assert _client("admin", "bootstrap-pass-1").get("/api/users").status_code == 200
    finally:
        os.environ.pop("ADMIN_PASSWORD", None)
        os.environ["AUTH_ENABLED"] = "0"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
