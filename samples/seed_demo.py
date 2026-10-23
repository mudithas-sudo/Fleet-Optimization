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
    38 parcels   delivered on time and late, a failed delivery waiting for review,
                 one rescheduled for tomorrow after review, one returned to the sender,
                 express / priority / window parcels, a collect-and-deliver, one that
                 no vehicle can carry, and three customer pickup requests that arrived
                 during the day (one already added to the live run, two still open),
                 plus eight ready-to-assign pending parcels for trying out vehicles
    history  parcel timelines, receipt references and photos, customer messages,
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
# pickup points sit on ordinary streets: a point inside the port enclosure snaps to a ferry/shipping road and the Routes API answers with a 3,000 km route
HARBOUR = {"lat": 6.9180, "lng": 79.8570, "label": "Union Place, Colombo 02"}
PETTAH_WH = {"lat": 6.9344, "lng": 79.8530, "label": "Pettah Warehouse"}

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

# ---------------------------------------------------------------- predefined routes
# code, name, zone centre, radius km, default driver, start time, recurring pickups (place, customer, cut-off, collect by)
ROUTE_TEMPLATES = [
    ("R-01", "Colombo Central", "union", 5, None, "08:00",
     [("galleface", "Hotel linen returns", "16:00", "17:30"), ("pettah", "Pettah wholesale dispatch", "15:00", "17:00")]),
    ("R-02", "Southern Corridor", "dehiwala", 7, "Kamal Silva", "08:30",
     [("lavinia", "Seaside Pharmacy", "14:00", "16:00")]),
    ("R-03", "Northern Corridor", "wattala", 7, None, "09:00",
     [("kiribathgoda", "Kiribathgoda Mart", "13:00", "15:00")]),
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


# -- more pending parcels, ready to assign to vehicles (small/medium fit a bike, car or van; large needs a van or truck)
parcel("p9", "FO-100131", "Pharmacy order", "Healthguard Pharmacy", "+94 11 269 1122", None, "borella", "small", (25, 20, 10), 1.5, priority="priority")
parcel("p10", "FO-100132", "Gym equipment, dumbbell set", "FitZone Rajagiriya", "+94 11 288 4410", "fitzone@example.com", "rajagiriya", "medium", (50, 30, 25), 20)
parcel("p11", "FO-100133", "Documents envelope", "Dr. S. Wickramasinghe", "+94 77 842 1190", "drsw@example.com", "kollupitiya", "small", (32, 24, 3), 0.3, priority="express")
parcel("p12", "FO-100134", "Bookshelf flat-pack", "Hansini Books", "+94 11 273 9004", None, "nugegoda", "large", (180, 35, 30), 32)
parcel("p13", "FO-100135", "Bakery cake boxes (x3)", "Sweet Crumbs", "+94 71 556 0917", "orders@sweetcrumbs.example.com", "dehiwala", "medium", (40, 40, 30), 5, fragile=True)
parcel("p14", "FO-100136", "Laptop charger", "N. Gunasekara", "+94 76 330 2288", "ngunase@example.com", "battaramulla", "small", (20, 12, 6), 0.4)
parcel("p15", "FO-100137", "Car battery", "Auto Care Malabe", "+94 11 241 7765", None, "malabe", "medium", (30, 20, 25), 14, notes="Heavy, lift with two hands.")
parcel("p16", "FO-100138", "Blood pressure monitors (x6)", "City Clinic Wattala", "+94 11 293 5501", None, "wattala", "small", (35, 25, 15), 4)

# -- customer pickup requests that arrived during the day (collected, then brought to the hub)
parcel("q1", "FO-100128", "Tea samples (pickup request)", "Lanka Tea Exports", "+94 11 233 4410", "ship@lankatea.example.com", "union", "small", (40, 30, 20), 6, request=True, cutoff_h=0.5, by_h=2.5)
parcel("q2", "FO-100129", "Signed contracts (pickup request)", "Havelock Legal", "+94 11 258 7720", None, "kollupitiya", "small", (35, 25, 5), 0.8, request=True, cutoff_h=0.1, by_h=1.6)
parcel("q3", "FO-100130", "Sample fabric roll (pickup request)", "Ceylon Weavers", "+94 71 330 8821", "orders@weavers.example.com", "dematagoda", "medium", (120, 25, 25), 9, request=True, cutoff_h=3.0, by_h=5.0)


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
    if x.get("request"):                         # a pickup request: the customer's address, back to the hub
        hub = delivery.get_depot()
        latest = NOW + x["by_h"] * HOUR
        with at(NOW - created_ago_h * HOUR):
            p = delivery.new_parcel({
                "name": d["name"], "trackingNumber": d["tracking"], "receiverName": d["receiver"],
                "recipientPhone": d["phone"], "receiverEmail": d["email"], "size": d["size"], "weightKg": d["kg"],
                "dimensionsCm": dict(zip("lwh", d["dims"])), "deadline": latest, "requestType": "pickup",
                "destination": {"lat": hub["lat"], "lng": hub["lng"], "label": hub["label"]},
                "pickup": {"lat": lat, "lng": lng, "label": label,
                           "earliest": NOW + x["cutoff_h"] * HOUR, "latest": latest}})
        P[key]["id"] = p["id"]
        return p
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
        stops = routing.sequence_stops(stops)            # the planner's visiting order (nearest next, pickups first)
        route = asyncio.run(routing.compute_route(stops, vehicle_type=vehicle["type"], presequenced=True))
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
        # signatures are captured by the separate e-signature process: we keep its reference
        ref = f"ESIG-{sum(map(ord, key)) * 37 % 900000 + 100000}" if how == "signature" else None
        how = "handed_over" if how == "signature" else how
        pho = photo_image(sum(map(ord, key)) % 50) if how in ("photo", "left_safe") else None
        delivery.save_pod(p, how, who, note, None, pho, trip["id"], lat, lng, reference=ref)
        delivery.set_parcel_status(p, "delivered")
    trip["deliveredCount"] = trip.get("deliveredCount", 0) + 1
    alert(trip, ts, "delivered", f"Delivered: {p['name']}" + (" (past deadline)" if late else ""))


def fail(trip, key, ts, reason, note=None):
    p = load(key)
    with at(ts):
        delivery.record_failed_attempt(p, reason, note, trip["id"])
    trip["failedCount"] = trip.get("failedCount", 0) + 1
    alert(trip, ts, "failed", f"Couldn't deliver {p['name']}: {delivery.FAIL_REASONS[reason]}")


def stop_time(trip, key, factor):
    """When the vehicle reaches this parcel's stop: the promised time stretched by `factor`
    (a bit of real-world slack), so the seeded day has believable plan-vs-actual numbers."""
    etas = {e["parcelId"]: e["eta"] for e in trip["plannedEtaPerStop"] if e["kind"] == "delivery"}
    return trip["startedAt"] + (etas[P[key]["id"]] - trip["startedAt"]) * factor


def run_length(trip, factor):
    etas = [e["eta"] for e in trip["plannedEtaPerStop"]]
    return (max(etas) - trip["startedAt"]) * factor + 3 * MIN


def finish(trip, ended, km_factor=1.03):
    trip["status"], trip["endedAt"] = "completed", ended
    alert(trip, ended, "completed", "Driver arrived — trip completed")
    log_positions(trip, trip["startedAt"], ended)
    # the sampled trail under-counts a little; the odometer-style figure is the plan plus the detour driven
    trip["actualDistanceMeters"] = trip["route"]["totalDistanceMeters"] * km_factor
    store.save_trip(trip)


# ---------------------------------------------------------------- the day
def main(reset: bool, with_users: bool):
    existing = db.all_entities("vehicles") or db.all_entities("parcels") or store.list_trips()
    if existing and not reset:
        sys.exit("Data already exists. Re-run with --reset to replace it (staff accounts are kept).")
    if reset:
        for table in ("parcels", "vehicles", "drivers", "pods", "notifications", "route_templates"):
            for e in db.all_entities(table):
                db.delete_entity(table, e["id"])
        db.clear_trips()

    vehicles = {v["name"]: delivery.new_vehicle(v) for v in VEHICLES}
    for name, phone, vehicle, extra in DRIVERS:
        delivery.new_driver({"name": name, "phone": phone, "vehicleId": vehicles[vehicle]["id"], **extra})

    # predefined routes: standing zones with a default driver and recurring pickups
    from app import route_templates
    by_name = {d["name"]: d for d in db.all_entities("drivers")}
    for code, name, centre, radius, driver, start, pickups in ROUTE_TEMPLATES:
        label, lat, lng = PLACES[centre]
        route_templates.save({
            "code": code, "name": name, "zone": {"lat": lat, "lng": lng, "label": label, "radiusKm": radius},
            "driverId": by_name[driver]["id"] if driver else None, "days": [0, 1, 2, 3, 4, 5] if code != "R-03" else [0, 1, 2, 3, 4],
            "startTime": start,
            "pickups": [{"label": PLACES[pl][0], "lat": PLACES[pl][1], "lng": PLACES[pl][2], "name": who, "readyBy": ready,
                         "collectBy": by, "size": "small"} for pl, who, ready, by in pickups]})

    # parcels: (key, created hours ago, deadline hours from now)
    plan = {
        "b1": (9, -6.2), "b2": (9, -6.0), "b3": (9, -5.6),
        "k1": (8, -3.9), "k2": (8, -3.6), "k3": (30, -4.5), "k4": (8, -3.2), "k5": (8, -2.9), "k6": (8, -2.6), "k7": (34, -3.0),
        "n1": (3, 0.6), "n2": (3, 1.6), "n3": (3, 2.2), "n4": (3, 2.0), "n5": (3, 2.8), "n6": (3, 3.5),
        "d1": (1.5, 3.0), "d2": (1.5, 4.0), "d3": (1.5, 5.0),
        "p1": (6, -0.75), "p2": (2, 0.42), "p3": (1, 4.0), "p4": (2, 5.0), "p5": (2, 8.0), "p6": (2, 9.0), "p7": (4, 10.0), "p8": (1, 3.0),
        "p9": (1.2, 2.5), "p10": (1.5, 6.0), "p11": (0.8, 1.5), "p12": (2, 8.0), "p13": (1, 4.5), "p14": (0.6, 5.5), "p15": (1.4, 7.0), "p16": (0.9, 3.5),
        "q1": (0.3, 2.5), "q2": (0.45, 1.6), "q3": (0.2, 5.0),
    }
    for key, (ago, due) in plan.items():
        new_parcel(key, ago, due)

    # a parcel that has already failed twice on earlier days
    for ago in (30, 26):
        with at(NOW - ago * HOUR):
            delivery.record_failed_attempt(load("k7"), "no_one_home", "Called twice, no answer", None)

    # ---- run 1: Bike 01 (Ruwan) — finished early this morning
    t = make_run("Ruwan Jayasinghe", ["b1", "b2", "b3"], created=NOW - 8.6 * HOUR, started=NOW - 7.5 * HOUR)
    f = 1.06
    deliver(t, "b1", stop_time(t, "b1", f), "signature", "Reception desk")
    deliver(t, "b2", stop_time(t, "b2", f), "handed_over", "S. Weerakoon")
    deliver(t, "b3", stop_time(t, "b3", f), "signature", "Lab technician")
    finish(t, t["startedAt"] + run_length(t, f))

    # ---- run 2: Van 02 (Kamal) — finished a couple of hours ago, one late, one failed, one returned
    t = make_run("Kamal Silva", ["k1", "k2", "k3", "k4", "k5", "k6", "k7"], created=NOW - 5.6 * HOUR, started=NOW - 5 * HOUR)
    f, span = 1.18, run_length(t, 1.18)                # a detour and slow traffic cost this run about 18%
    alert(t, t["startedAt"] + .30 * span, "deviation", "Vehicle is 190 m off the planned route", path_point(t["route"], .25), 190)
    alert(t, t["startedAt"] + .32 * span, "reroute", "Route recalculated from current position (11.2 km remaining)")
    alert(t, t["startedAt"] + .33 * span, "back_on_route", "Vehicle is back on the planned route")
    t["routeVersion"] = 2
    deliver(t, "k1", stop_time(t, "k1", f), "signature", "A. Wijesinghe")
    deliver(t, "k2", stop_time(t, "k2", f), "handed_over", "Librarian")
    deliver(t, "k3", stop_time(t, "k3", f), "photo", None, note="Left with the neighbour at no. 14", late=True)
    deliver(t, "k4", stop_time(t, "k4", f), "signature", "Shop assistant")
    fail(t, "k5", stop_time(t, "k5", f), "no_one_home", "Gate locked, phone off")
    deliver(t, "k6", stop_time(t, "k6", f), "handed_over", "Workshop foreman")
    fail(t, "k7", stop_time(t, "k7", f), "refused", "Receiver said they no longer need it")      # 3rd failure + refusal -> review
    finish(t, t["startedAt"] + span, km_factor=1.12)
    with at(t["endedAt"] + 20 * MIN):                    # dispatch reviews the refused cake: return it to the sender
        delivery.review_failed(load("k7"), "return", "Receiver no longer needs it — returned to sender", actor="dispatcher1")

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
    live_position = {**path_point(t["route"], frac), "ts": NOW}
    t["lastPosition"] = live_position
    store.save_trip(t)

    # a pickup request came in after this run had left the hub: dispatch added it to the live run
    from app import replan
    with at(NOW - 12 * MIN):
        asyncio.run(replan.replan(t["id"], add_ids=[P["q2"]["id"]]))
    t = store.get_trip(t["id"])
    t["lastPosition"] = live_position               # where the vehicle is: unchanged by the re-plan
    store.save_trip(t)

    # ---- run 4: Car 01 (Dilani) — planned, not started
    make_run("Dilani Fernando", ["d1", "d2", "d3"], created=NOW - 20 * MIN)

    # ---- the loading bay: the warehouse has started loading the scheduled run (one parcel ticked)
    from app import warehouse
    planned = next(t for t in store.list_trips() if t["status"] == "planned")
    first = sorted(planned["parcelIds"])[0]
    warehouse.set_loaded(db.load_entity("parcels", first), True, "warehouse1")

    # ---- an earlier failed delivery was reviewed: the customer asked for tomorrow morning
    tomorrow = delivery.day_start(NOW) + 86400 + 9 * HOUR
    with at(NOW - 0.8 * HOUR):
        delivery.record_failed_attempt(load("p8"), "no_one_home", "Surgery closed", None)
    with at(NOW - 0.6 * HOUR):
        delivery.review_failed(load("p8"), "reschedule", "Spoke to the surgery: deliver tomorrow morning",
                               tomorrow, actor="dispatcher1")

    # ---- the failed hamper waits for review; the cake shows under "Returned"; the glasses are held until tomorrow
    assert load("k7")["status"] == "returned", load("k7")["status"]
    assert load("k5")["status"] == "failed" and load("k5")["review"]["state"] == "pending"
    assert load("p8")["status"] == "awaiting_redelivery" and load("p8")["holdUntil"]
    assert load("q2")["status"] == "in_transit" and load("q1")["status"] == "pending"

    # nothing in the sample day may be anywhere but around the hub (a stray point would draw a route to another country)
    hub = delivery.get_depot()
    for tr in store.list_trips():
        for pt in [*tr["route"]["orderedStops"], *([tr["lastPosition"]] if tr.get("lastPosition") else [])]:
            assert routing._haversine_m(hub, pt) < 150_000, (
                f"run {tr['id']} has a point far from the hub: {pt}; stops: "
                + "; ".join(f"{s.get('label')} ({s['lat']:.4f}, {s['lng']:.4f})" for s in tr["route"]["orderedStops"]))

    if with_users:
        from app import auth
        for username, name, role in (("admin", "Administrator", "admin"), ("supervisor1", "Sunethra Supervisor", "supervisor"),
                                     ("dispatcher1", "Dinesh Dispatcher", "dispatcher"), ("warehouse1", "Wasantha Warehouse", "warehouse"),
                                     ("support1", "Sanduni Support", "support"), ("fleet1", "Fathima Fleet", "fleet"),
                                     ("analyst1", "Anura Analyst", "analyst"), ("viewer1", "Vimal Viewer", "viewer")):
            if not auth.find_by_username(username):
                u = auth.create_user(username, name, role, "demo-pass-123")
                u["demo"] = True                  # listed on the demo sign-in page (when DEMO_ACCOUNTS=1)
                db.save_entity("users", u)

    if time.localtime().tm_hour < 10:
        print("NOTE: it is before 10:00, so the earlier runs of the sample day fall on yesterday and the dashboard shows"
              " fewer finished runs. For the best demo data, load it after 10:00.")
    counts = {k: len(db.all_entities(k)) for k in ("vehicles", "drivers", "parcels", "notifications")}
    print(f"Added {counts['vehicles']} vehicles, {counts['drivers']} drivers, {counts['parcels']} parcels, "
          f"4 runs and {counts['notifications']} customer messages.")
    print("Predefined routes: R-01 Colombo Central, R-02 Southern Corridor, R-03 Northern Corridor (Runs page > Run today's route).")
    print("Pickup requests: FO-100129 is on the live run, FO-100128 and FO-100130 are open (open one to see the best-run suggestions).")
    print("Live now: Nuwan Perera on Van 01. Finished: Kamal Silva, Ruwan Jayasinghe. Scheduled: Dilani Fernando.")
    print("Try the tracking page with FO-100112 (delivered) or FO-100114 (out for delivery).")
    if with_users:
        print("Demo staff accounts (set AUTH_ENABLED=1 to use them), password demo-pass-123: admin, supervisor1, dispatcher1, "
              "warehouse1, support1, fleet1, analyst1, viewer1")
    print("Restart the server (or refresh the page) to see the new data.")


if __name__ == "__main__":
    main("--reset" in sys.argv, "--users" in sys.argv)
