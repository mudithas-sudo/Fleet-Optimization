"""Load a complete sample working day so every screen has something to show.

    venv\\Scripts\\python samples\\seed_demo.py --reset           (Windows)
    venv/bin/python samples/seed_demo.py --reset                (macOS/Linux)

Options
    --reset         wipe parcels, vehicles, drivers, runs, positions, proofs and
                    customer messages first (staff accounts are kept)
    --users         also create demo staff accounts (see the end of the output)
    --real-routes   route the seeded runs through the Routes API (uses your key;
                    default is offline straight-line routes, so seeding costs nothing)

What you get (all times are relative to *now*, so re-run it before a demo):
    4 runs   2 finished earlier today, 1 live right now, 1 scheduled
    27 parcels   delivered on time and late, a failed attempt waiting to be
                 rescheduled, one returned to the sender, express / priority /
                 window parcels, a collect-and-deliver, one that no vehicle can carry
    history  parcel timelines, signatures and photos, customer messages,
             vehicle position trails (drag the dashboard time bar back to see them)
"""

import asyncio
import contextlib
import math
import os
import random
import struct
import sys
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REAL_ROUTES = "--real-routes" in sys.argv
if not REAL_ROUTES:
    os.environ["FAKE_ROUTES"] = "1"          # offline, no API calls

from app import db, delivery, routing, store  # noqa: E402

NOW = time.time()
MIN, HOUR = 60, 3600
_real_time = time.time


@contextlib.contextmanager
def at(ts: float):
    """Run code as if it were `ts`, so histories, alerts and messages carry real-looking times."""
    time.time = lambda: ts
    try:
        yield
    finally:
        time.time = _real_time


# ---------------------------------------------------------------- places (Colombo)
PLACES = {
    "majestic": ("Majestic City, Bambalapitiya", 6.8935, 79.8553), "nugegoda": ("Nugegoda Junction", 6.8649, 79.8997),
    "rajagiriya": ("Rajagiriya Flyover", 6.9097, 79.8918), "borella": ("Borella Junction", 6.9147, 79.8776),
    "maharagama": ("Maharagama Town", 6.8480, 79.9265), "malabe": ("SLIIT Campus, Malabe", 6.9061, 79.9694),
    "dehiwala": ("Dehiwala Junction", 6.8511, 79.8650), "reid": ("Reid Avenue, Colombo 07", 6.9066, 79.8628),
    "battaramulla": ("Battaramulla Junction", 6.8993, 79.9179), "lavinia": ("Mount Lavinia", 6.8389, 79.8636),
    "wattala": ("Wattala Junction", 6.9890, 79.8913), "kiribathgoda": ("Kiribathgoda", 6.9780, 79.9300),
    "galleface": ("Galle Face Hotel", 6.9271, 79.8449), "ratmalana": ("Ratmalana Industrial Zone", 6.8200, 79.8860),
    "dockyard": ("Colombo Dockyard, Graving Dock", 6.9525, 79.8500), "pettah": ("Pettah Market", 6.9344, 79.8530),
    "kollupitiya": ("Kollupitiya Junction", 6.9115, 79.8490), "havelock": ("Havelock Town", 6.8890, 79.8690),
    "narahenpita": ("Narahenpita", 6.8940, 79.8770), "peliyagoda": ("Peliyagoda", 6.9640, 79.8800),
    "thalawathugoda": ("Thalawathugoda", 6.8730, 79.9250), "union": ("Union Place, Colombo 02", 6.9180, 79.8570),
    "dematagoda": ("Dematagoda", 6.9370, 79.8780), "slave": ("Slave Island", 6.9230, 79.8560),
    "kotte": ("Sri Jayawardenepura Kotte", 6.8900, 79.9013), "moratuwa": ("Moratuwa", 6.7730, 79.8816),
}
HARBOUR = {"lat": 6.9497, "lng": 79.8468, "label": "Colombo Harbour"}
PETTAH_WH = {"lat": 6.9380, "lng": 79.8590, "label": "Pettah Warehouse"}

# ---------------------------------------------------------------- fleet
VEHICLES = [
    {"name": "Van 01", "plate": "CAB-4471", "type": "van"},
    {"name": "Van 02", "plate": "CAB-5120", "type": "van"},
    {"name": "Car 01", "plate": "KY-3308", "type": "car"},
    {"name": "Bike 01", "plate": "BKX-210", "type": "bike"},
    {"name": "Truck 01", "plate": "LB-9021", "type": "truck"},
]
DRIVERS = [   # name, phone, vehicle, extras
    ("Nuwan Perera", "+94 77 123 4567", "Van 01", {}),
    ("Kamal Silva", "+94 71 555 0182", "Van 02", {}),
    ("Dilani Fernando", "+94 76 220 9914", "Car 01", {"shiftStart": "05:00", "shiftEnd": "23:30"}),
    ("Ruwan Jayasinghe", "+94 70 441 7765", "Bike 01", {"maxStops": 3}),
    ("Chaminda Rathnayake", "+94 75 318 2290", "Truck 01", {"onLeave": True}),
]

# ---------------------------------------------------------------- parcels
# key: (tracking, parcel, receiver, phone, email, place, size, (L,W,H) cm, kg, extras)
P = {}


def parcel(key, tracking, name, receiver, phone, email, place, size, dims, kg, **extra):
    P[key] = dict(tracking=tracking, name=name, receiver=receiver, phone=phone, email=email, place=place,
                  size=size, dims=dims, kg=kg, extra=extra)


# -- bike run (finished early this morning)
parcel("b1", "FO-100101", "Legal documents", "Attorney M. Gunawardena", "+94 11 267 4400", None, "borella", "small", (32, 24, 4), 0.4)
parcel("b2", "FO-100102", "Passport application", "S. Weerakoon", "+94 77 612 0044", "sweerakoon@example.com", "union", "small", (30, 22, 3), 0.2)
parcel("b3", "FO-100103", "Blood test kit", "Lanka Hospitals Lab", "+94 11 543 0000", None, "narahenpita", "small", (25, 18, 12), 0.9, fragile=True)
# -- Van 02, Kamal (finished)
parcel("k1", "FO-100104", "Router #4411", "A. Wijesinghe", "+94 77 310 4421", "awije@example.com", "majestic", "small", (30, 22, 10), 1.2)
parcel("k2", "FO-100105", "Textbooks (box)", "Royal College Library", "+94 11 269 5301", None, "reid", "medium", (45, 35, 30), 18)
parcel("k3", "FO-100106", "Wedding invitations", "N. Jayatilaka", "+94 71 902 1188", "njaya@example.com", "dehiwala", "small", (36, 26, 12), 4)     # delivered late
parcel("k4", "FO-100107", "Monitor 27 inch", "Tech Hub Nugegoda", "+94 11 281 7700", None, "nugegoda", "medium", (70, 45, 20), 6.5, fragile=True)
parcel("k5", "FO-100108", "Gift hamper", "P. Karunaratne", "+94 75 604 3318", "pkaru@example.com", "lavinia", "medium", (50, 40, 30), 7)    # nobody home
parcel("k6", "FO-100109", "Spare tyres (x2)", "Auto Lanka", "+94 11 262 5510", None, "ratmalana", "large", (70, 70, 25), 24)
parcel("k7", "FO-100110", "Birthday cake box", "D. Seneviratne", "+94 77 405 1190", "dsene@example.com", "kotte", "medium", (40, 40, 25), 3, fragile=True)  # 3rd failure
# -- Van 01, Nuwan (live)
parcel("n1", "FO-100111", "Laptop, 14 inch", "R. de Mel", "+94 76 530 9012", "rdemel@example.com", "rajagiriya", "small", (38, 28, 8), 2.1, fragile=True, priority="express")
parcel("n2", "FO-100112", "SIM starter kits (x20)", "S. Fonseka", "+94 71 880 2210", None, "nugegoda", "small", (25, 18, 12), 0.8)
parcel("n3", "FO-100113", "Printer cartridges (collect and deliver)", "Ceylon Print Co.", "+94 11 245 8800", "orders@ceylonprint.example.com", "galleface", "small", (30, 20, 15), 3, pickup=HARBOUR)
parcel("n4", "FO-100114", "Microwave oven", "N. Ratnayake", "+94 77 912 0345", None, "maharagama", "medium", (55, 45, 35), 14, priority="priority")
parcel("n5", "FO-100115", "Ceramic dinner set", "H. Abeysekara", "+94 75 220 7743", "habey@example.com", "battaramulla", "medium", (50, 40, 30), 9, fragile=True, window_h=-0.2)
parcel("n6", "FO-100116", "Office chair", "SLIIT Malabe", "+94 11 754 4801", None, "malabe", "medium", (65, 60, 110), 12, notes="Gate 2, ask for the stores office.")
# -- Car 01, Dilani (scheduled)
parcel("d1", "FO-100117", "Phone case bundle", "M. Dissanayake", "+94 76 777 1020", "mdiss@example.com", "kollupitiya", "small", (28, 20, 8), 0.6)
parcel("d2", "FO-100118", "Kitchen blender", "R. Mendis", "+94 71 220 6643", None, "havelock", "medium", (35, 25, 40), 4.2)
parcel("d3", "FO-100119", "Yoga mats (x4)", "Lotus Studio", "+94 11 258 9090", "hello@lotus.example.com", "thalawathugoda", "medium", (70, 25, 25), 5)
# -- still to plan
parcel("p1", "FO-100120", "Insurance forms", "Ceylinco Branch", "+94 11 238 8388", None, "slave", "small", (32, 24, 4), 0.5)       # overdue
parcel("p2", "FO-100121", "Tablet 10 inch", "K. Mendis", "+94 77 114 9032", "kmendis@example.com", "peliyagoda", "small", (28, 20, 4), 0.6, priority="priority")   # due soon
parcel("p3", "FO-100122", "Wedding saree set", "T. Ariyaratne", "+94 71 440 2217", "tari@example.com", "dematagoda", "small", (45, 35, 12), 2, priority="express", window_h=1.5)
parcel("p4", "FO-100123", "Spare parts crate (collect and deliver)", "Ratmalana Works", "+94 11 262 0071", None, "ratmalana", "medium", (60, 40, 40), 22, pickup=PETTAH_WH)
parcel("p5", "FO-100124", "Standing desk", "J. Abeysekara", "+94 77 701 8844", "jabey@example.com", "kiribathgoda", "large", (140, 70, 15), 28)
parcel("p6", "FO-100125", "Refrigerator 190 L", "K. Perera", "+94 71 220 7712", None, "moratuwa", "large", (60, 62, 150), 45, notes="Second floor, no lift.")
parcel("p7", "FO-100126", "Shipping pallet, machinery", "Colombo Dockyard", "+94 11 244 8000", None, "dockyard", "large", (600, 250, 250), 900)   # too big for any vehicle
parcel("p8", "FO-100127", "Reading glasses", "Dr. Fernando", "+94 77 336 0912", "drf@example.com", "majestic", "small", (18, 8, 5), 0.2)


# ---------------------------------------------------------------- small PNG maker (no dependencies)
def _png(width, height, rows):
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _data_url(png: bytes) -> str:
    import base64
    return "data:image/png;base64," + base64.b64encode(png).decode()


def signature_image(seed: int) -> str:
    """A hand-drawn-looking scribble on white."""
    rnd = random.Random(seed)
    w, h = 240, 80
    px = [[255] * w for _ in range(h)]
    x, y = 14, 40
    for i in range(70):
        nx = x + rnd.randint(2, 6)
        ny = max(8, min(h - 8, y + int(math.sin(i / 2.2) * 9) + rnd.randint(-4, 4)))
        steps = max(abs(nx - x), abs(ny - y), 1)
        for s in range(steps + 1):
            cx, cy = x + (nx - x) * s // steps, y + (ny - y) * s // steps
            for dx in (0, 1):
                for dy in (0, 1):
                    if 0 <= cx + dx < w and 0 <= cy + dy < h:
                        px[cy + dy][cx + dx] = 35
        x, y = min(nx, w - 10), ny
    return _data_url(_png(w, h, px))


def photo_image(seed: int) -> str:
    """A placeholder 'doorstep photo': soft gradient with a parcel-shaped box."""
    w, h = 200, 140
    px = [[int(200 - 60 * (yy / h) + 15 * math.sin(xx / 18 + seed)) for xx in range(w)] for yy in range(h)]
    for yy in range(70, 118):
        for xx in range(60, 140):
            px[yy][xx] = 150 if (yy < 76 or yy > 112 or xx < 66 or xx > 134) else 175
    for yy in range(70, 118):
        px[yy][98:102] = [95] * 4
    return _data_url(_png(w, h, px))


# ---------------------------------------------------------------- building blocks
def new_parcel(key, created_ago_h, deadline_in_h):
    d = P[key]
    label, lat, lng = PLACES[d["place"]]
    x = d["extra"]
    with at(NOW - created_ago_h * HOUR):
        p = delivery.new_parcel({
            "name": d["name"], "trackingNumber": d["tracking"], "receiverName": d["receiver"],
            "recipientPhone": d["phone"], "receiverEmail": d["email"], "size": d["size"], "weightKg": d["kg"],
            "dimensionsCm": dict(zip("lwh", d["dims"])), "deadline": NOW + deadline_in_h * HOUR,
            "fragile": x.get("fragile", False), "notes": x.get("notes"), "priority": x.get("priority", "standard"),
            "pickup": x.get("pickup"), "destination": {"lat": lat, "lng": lng, "label": label},
            "windowStart": NOW + x["window_h"] * HOUR if "window_h" in x else None,
        })
    P[key]["id"] = p["id"]
    return p


def load(key):
    return db.load_entity("parcels", P[key]["id"])


def alert(trip, ts, kind, message, position=None, dist=0):
    trip["alerts"].append({"id": f"a-{len(trip['alerts']):04d}{trip['id'][-3:]}", "tripId": trip["id"], "type": kind,
                           "distanceMeters": dist, "position": position, "ts": ts, "message": message})


def path_point(route, frac):
    path = route["path"]
    i = min(len(path) - 1, max(0, int(frac * (len(path) - 1))))
    return {"lat": path[i][0], "lng": path[i][1]}


def log_positions(trip, t0, t1, frac_end=1.0):
    """One logged position every 30 s, moving along the route (with a short pause at each stop)."""
    n = max(1, int((t1 - t0) // 30))
    for k in range(n + 1):
        ts = t0 + k * 30
        frac = (k / n) * frac_end
        pt = path_point(trip["route"], frac)
        db.add_position(trip["id"], pt["lat"], pt["lng"], ts)


def make_run(driver_name, keys, *, created, started=None, ended=None, live_frac=None):
    """Create a run with its parcels assigned; times are absolute epoch seconds."""
    drivers = {d["name"]: d for d in db.all_entities("drivers")}
    driver = drivers[driver_name]
    vehicle = db.load_entity("vehicles", driver["vehicleId"])
    parcels = [load(k) for k in keys]
    with at(created):
        stops = delivery.build_run_stops(parcels)
        route = asyncio.run(routing.compute_route(stops, vehicle_type=vehicle["type"]))
        trip = store.new_trip(stops, route, vehicle_type=vehicle["type"])
        trip.update({
            "vehicleId": vehicle["id"], "driverId": driver["id"], "parcelIds": [p["id"] for p in parcels],
            "plannedEtaPerStop": delivery.compute_planned_etas(route, {p["id"]: p for p in parcels}, started or created + 15 * MIN),
            "status": "planned", "startedAt": None, "endedAt": None,
            "route": {**route, "briefing": f"{len(stops) - 1} stops from the hub in {vehicle['name']}. "
                                            "Follow the listed order and tick each parcel off at the door."},
        })
        store.save_trip(trip)
        for p in parcels:
            delivery.set_parcel_status(p, "assigned", trip_id=trip["id"])
    if started:
        trip["status"], trip["startedAt"] = "active", started
        alert(trip, started, "started", "Driver started the trip")
        store.save_trip(trip)
        for k in keys:
            with at(started + 5):
                delivery.set_parcel_status(load(k), "in_transit", trip_id=trip["id"])
    return trip


def deliver(trip, key, ts, how, who, *, note=None, late=False):
    """Mark delivered at `ts` with proof of delivery."""
    p = load(key)
    label, lat, lng = PLACES[P[key]["place"]]
    with at(ts):
        sig = signature_image(sum(map(ord, key))) if how == "signature" else None
        pho = photo_image(sum(map(ord, key)) % 50) if how in ("photo", "left_safe") else None
        delivery.save_pod(p, how, who, note, sig, pho, trip["id"], lat, lng)
        delivery.set_parcel_status(p, "delivered")
    trip["deliveredCount"] = trip.get("deliveredCount", 0) + 1
    alert(trip, ts, "delivered", f"Delivered: {p['name']}" + (" (past deadline)" if late else ""))


def fail(trip, key, ts, reason, note=None):
    p = load(key)
    with at(ts):
        delivery.record_failed_attempt(p, reason, note, trip["id"])
    trip["failedCount"] = trip.get("failedCount", 0) + 1
    alert(trip, ts, "failed", f"Couldn't deliver {p['name']}: {delivery.FAIL_REASONS[reason]}")


def finish(trip, ended):
    trip["status"], trip["endedAt"] = "completed", ended
    alert(trip, ended, "completed", "Driver arrived — trip completed")
    log_positions(trip, trip["startedAt"], ended)
    trip["actualDistanceMeters"] = store.actual_distance_m(trip["id"])
    store.save_trip(trip)


# ---------------------------------------------------------------- the day
def main(reset: bool, with_users: bool):
    existing = db.all_entities("vehicles") or db.all_entities("parcels") or store.list_trips()
    if existing and not reset:
        sys.exit("Data already exists. Re-run with --reset to replace it (staff accounts are kept).")
    if reset:
        for table in ("parcels", "vehicles", "drivers", "pods", "notifications"):
            for e in db.all_entities(table):
                db.delete_entity(table, e["id"])
        db.clear_trips()

    vehicles = {v["name"]: delivery.new_vehicle(v) for v in VEHICLES}
    for name, phone, vehicle, extra in DRIVERS:
        delivery.new_driver({"name": name, "phone": phone, "vehicleId": vehicles[vehicle]["id"], **extra})

    # parcels: (key, created hours ago, deadline hours from now)
    plan = {
        "b1": (9, -6.2), "b2": (9, -6.0), "b3": (9, -5.6),
        "k1": (8, -3.9), "k2": (8, -3.6), "k3": (30, -4.5), "k4": (8, -3.2), "k5": (8, -2.9), "k6": (8, -2.6), "k7": (34, -3.0),
        "n1": (3, 0.6), "n2": (3, 1.6), "n3": (3, 2.2), "n4": (3, 2.0), "n5": (3, 2.8), "n6": (3, 3.5),
        "d1": (1.5, 3.0), "d2": (1.5, 4.0), "d3": (1.5, 5.0),
        "p1": (6, -0.75), "p2": (2, 0.42), "p3": (1, 4.0), "p4": (2, 5.0), "p5": (2, 8.0), "p6": (2, 9.0), "p7": (4, 10.0), "p8": (1, 3.0),
    }
    for key, (ago, due) in plan.items():
        new_parcel(key, ago, due)

    # a parcel that has already failed twice on earlier days
    for ago in (30, 26):
        with at(NOW - ago * HOUR):
            delivery.record_failed_attempt(load("k7"), "no_one_home", "Called twice, no answer", None)

    # ---- run 1: Bike 01 (Ruwan) — finished early this morning
    t = make_run("Ruwan Jayasinghe", ["b1", "b2", "b3"], created=NOW - 8.6 * HOUR, started=NOW - 7.5 * HOUR)
    deliver(t, "b1", NOW - 7.2 * HOUR, "signature", "Reception desk")
    deliver(t, "b2", NOW - 6.8 * HOUR, "handed_over", "S. Weerakoon")
    deliver(t, "b3", NOW - 6.4 * HOUR, "signature", "Lab technician")
    finish(t, NOW - 6.1 * HOUR)

    # ---- run 2: Van 02 (Kamal) — finished a couple of hours ago, one late, one failed, one returned
    t = make_run("Kamal Silva", ["k1", "k2", "k3", "k4", "k5", "k6", "k7"], created=NOW - 5.6 * HOUR, started=NOW - 5 * HOUR)
    alert(t, NOW - 4.6 * HOUR, "deviation", "Vehicle is 190 m off the planned route", path_point(t["route"], .25), 190)
    alert(t, NOW - 4.5 * HOUR, "reroute", "Route recalculated from current position (11.2 km remaining)")
    alert(t, NOW - 4.48 * HOUR, "back_on_route", "Vehicle is back on the planned route")
    t["routeVersion"] = 2
    deliver(t, "k1", NOW - 4.4 * HOUR, "signature", "A. Wijesinghe")
    deliver(t, "k2", NOW - 4.1 * HOUR, "handed_over", "Librarian")
    deliver(t, "k3", NOW - 3.8 * HOUR, "photo", None, note="Left with the neighbour at no. 14", late=True)
    deliver(t, "k4", NOW - 3.5 * HOUR, "signature", "Shop assistant")
    fail(t, "k5", NOW - 3.2 * HOUR, "no_one_home", "Gate locked, phone off")
    deliver(t, "k6", NOW - 2.9 * HOUR, "handed_over", "Workshop foreman")
    fail(t, "k7", NOW - 2.6 * HOUR, "refused", "Receiver said they no longer need it")      # 3rd failure + refusal -> returned
    finish(t, NOW - 2.3 * HOUR)
    with at(NOW - 2 * HOUR):                    # dispatch gives the gift hamper another go this evening
        k5 = load("k5")
        k5["deadline"] = NOW + 3.5 * HOUR
        k5["deadlineMissed"] = False
        delivery.add_event(k5, "rescheduled", "Rescheduled for delivery this evening")
        db.save_entity("parcels", k5)

    # ---- run 3: Van 01 (Nuwan) — live right now
    started = NOW - 50 * MIN
    t = make_run("Nuwan Perera", ["n1", "n2", "n3", "n4", "n5", "n6"], created=NOW - 70 * MIN, started=started)
    alert(t, started + 25 * MIN, "deviation", "Vehicle is 150 m off the planned route", path_point(t["route"], .15), 150)
    alert(t, started + 28 * MIN, "reroute", "Route recalculated from current position (22.4 km remaining)")
    alert(t, started + 28.5 * MIN, "back_on_route", "Vehicle is back on the planned route")
    t["routeVersion"] = 2
    with at(started + 18 * MIN):
        delivery.set_parcel_status(load("n3"), "picked_up")
        t["collectedCount"] = 1
        alert(t, started + 18 * MIN, "collected", "Collected: Printer cartridges (collect and deliver)")
    deliver(t, "n1", started + 14 * MIN, "signature", "R. de Mel")
    deliver(t, "n2", started + 36 * MIN, "photo", None, note="Left with the security desk")
    frac = 0.4
    log_positions(t, started, NOW, frac_end=frac)
    t["lastPosition"] = {**path_point(t["route"], frac), "ts": NOW}
    store.save_trip(t)

    # ---- run 4: Car 01 (Dilani) — planned, not started
    make_run("Dilani Fernando", ["d1", "d2", "d3"], created=NOW - 20 * MIN)

    # ---- the failed hamper waits at the hub; the returned cake shows under "Returned"
    p = load("k7")
    assert p["status"] == "returned", p["status"]
    assert load("k5")["status"] == "awaiting_redelivery"

    if with_users:
        from app import auth
        for username, name, role in (("admin", "Administrator", "admin"), ("supervisor1", "Sunethra Supervisor", "supervisor"),
                                     ("dispatcher1", "Dinesh Dispatcher", "dispatcher"), ("viewer1", "Vimal Viewer", "viewer")):
            if not auth.find_by_username(username):
                auth.create_user(username, name, role, "demo-pass-123")

    counts = {k: len(db.all_entities(k)) for k in ("vehicles", "drivers", "parcels", "notifications")}
    print(f"Added {counts['vehicles']} vehicles, {counts['drivers']} drivers, {counts['parcels']} parcels, "
          f"4 runs and {counts['notifications']} customer messages.")
    print("Live now: Nuwan Perera on Van 01. Finished: Kamal Silva, Ruwan Jayasinghe. Scheduled: Dilani Fernando.")
    print("Try the tracking page with FO-100112 (delivered) or FO-100114 (out for delivery).")
    if with_users:
        print("Demo staff accounts (set AUTH_ENABLED=1 to use them): admin, supervisor1, dispatcher1, viewer1 - password demo-pass-123")
    print("Restart the server (or refresh the page) to see the new data.")


if __name__ == "__main__":
    main("--reset" in sys.argv, "--users" in sys.argv)
