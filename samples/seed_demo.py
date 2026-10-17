"""Load sample vehicles, drivers and parcels so the console has something to show.

    venv\\Scripts\\python samples\\seed_demo.py            (Windows)
    venv/bin/python samples/seed_demo.py                 (macOS/Linux)
    ... --reset     wipe parcels, vehicles and drivers first

Coordinates are fixed (Colombo), so no Maps key is needed to seed. Deadlines are
set relative to now, so re-run before a demo. Runs are not created here — plan
them in the console ("Suggest runs").
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db, delivery  # noqa: E402

VEHICLES = [
    {"name": "Van 01", "plate": "CAB-4471", "type": "van"},
    {"name": "Van 02", "plate": "CAB-5120", "type": "van"},
    {"name": "Car 01", "plate": "KY-3308", "type": "car"},
    {"name": "Bike 01", "plate": "BKX-210", "type": "bike"},
]
DRIVERS = [
    ("Nuwan Perera", "+94 77 123 4567", "Van 01"),
    ("Kamal Silva", "+94 71 555 0182", "Van 02"),
    ("Dilani Fernando", "+94 76 220 9914", "Car 01"),
    ("Ruwan Jayasinghe", "+94 70 441 7765", "Bike 01"),
]
DRIVER_LIMITS = {"Ruwan Jayasinghe": {"maxStops": 3}}      # the bike rider takes at most 3 stops a run

HARBOUR = {"lat": 6.9497, "lng": 79.8468, "label": "Colombo Harbour"}
PETTAH_WH = {"lat": 6.9380, "lng": 79.8590, "label": "Pettah Warehouse"}

# tracking, parcel, receiver, phone, address, lat, lng, size, (L, W, H) cm, kg, due in h, extras
PARCELS = [
    ("FO-100001", "Router #4411", "A. Wijesinghe", "+94 77 310 4421", "Majestic City, Bambalapitiya", 6.8935, 79.8553, "small", (30, 22, 10), 1.2, 2, {}),
    ("FO-100002", "SIM starter kits (x20)", "S. Fonseka", "+94 71 880 2210", "Nugegoda Junction", 6.8649, 79.8997, "small", (25, 18, 12), 0.8, 3, {"priority": "priority"}),
    ("FO-100003", "Laptop, 14 inch", "R. de Mel", "+94 76 530 9012", "Rajagiriya Flyover", 6.9097, 79.8918, "small", (38, 28, 8), 2.1, 2, {"fragile": True, "priority": "express", "email": "rdemel@example.com"}),
    ("FO-100004", "Legal documents", "Attorney M. Gunawardena", "+94 11 267 4400", "Borella Junction", 6.9147, 79.8776, "small", (32, 24, 4), 0.4, 1.5, {"notes": "Hand to the receptionist, signature required."}),
    ("FO-100005", "Microwave oven", "N. Ratnayake", "+94 77 912 0345", "Maharagama Town", 6.8480, 79.9265, "medium", (55, 45, 35), 14, 5, {}),
    ("FO-100006", "Office chair", "SLIIT Malabe", "+94 11 754 4801", "SLIIT Campus, Malabe", 6.9061, 79.9694, "medium", (65, 60, 110), 12, 6, {"notes": "Gate 2, ask for the stores office."}),
    ("FO-100007", "Ceramic dinner set", "P. Karunaratne", "+94 75 604 3318", "Dehiwala Junction", 6.8511, 79.8650, "medium", (50, 40, 30), 9, 4, {"fragile": True, "window_h": 1.5}),
    ("FO-100008", "Textbooks (box)", "Royal College Library", "+94 11 269 5301", "Reid Avenue, Colombo 07", 6.9066, 79.8628, "medium", (45, 35, 30), 18, 3, {}),
    ("FO-100009", "Standing desk", "J. Abeysekara", "+94 77 701 8844", "Battaramulla Junction", 6.8993, 79.9179, "large", (140, 70, 15), 28, 8, {}),
    ("FO-100010", "Refrigerator 190 L", "K. Mendis", "+94 71 220 6643", "Mount Lavinia", 6.8389, 79.8636, "large", (60, 62, 150), 45, 7, {"notes": "Second floor, no lift."}),
    ("FO-100011", "Display shelf units (x3)", "Cargills, Wattala", "+94 11 293 0050", "Wattala Junction", 6.9890, 79.8913, "large", (180, 40, 30), 36, 6, {}),
    ("FO-100012", "Sofa, 3 seater", "D. Seneviratne", "+94 77 405 1190", "Kiribathgoda", 6.9780, 79.9300, "large", (200, 90, 85), 55, 9, {}),
    # collected from a supplier on the way, then delivered
    ("FO-100013", "Printer cartridges (collect and deliver)", "Ceylon Print Co.", "+94 11 245 8800", "Galle Face Hotel", 6.9271, 79.8449, "small", (30, 20, 15), 3, 4, {"pickup": HARBOUR}),
    ("FO-100014", "Spare parts crate (collect and deliver)", "Ratmalana Works", "+94 11 262 0071", "Ratmalana Industrial Zone", 6.8200, 79.8860, "medium", (60, 40, 40), 22, 6, {"pickup": PETTAH_WH}),
    # cannot go on any vehicle in this fleet — shows up as "unassigned"
    ("FO-100015", "Shipping pallet, machinery", "Colombo Dockyard", "+94 11 244 8000", "Colombo Dockyard, Graving Dock", 6.9525, 79.8500, "large", (600, 250, 250), 900, 10, {}),
]


def main(reset: bool):
    if reset:
        for table in ("parcels", "vehicles", "drivers"):
            for e in db.all_entities(table):
                db.delete_entity(table, e["id"])
    elif db.all_entities("vehicles") or db.all_entities("parcels"):
        sys.exit("Data already exists. Re-run with --reset to replace it.")

    vehicles = {v["name"]: delivery.new_vehicle(v) for v in VEHICLES}
    for name, phone, vehicle in DRIVERS:
        delivery.new_driver({"name": name, "phone": phone, "vehicleId": vehicles[vehicle]["id"],
                             **DRIVER_LIMITS.get(name, {})})

    now = time.time()
    for trk, name, receiver, phone, address, lat, lng, size, dims, kg, due_h, extra in PARCELS:
        delivery.new_parcel({
            "name": name, "trackingNumber": trk, "receiverName": receiver,
            "recipientPhone": phone, "size": size, "weightKg": kg,
            "dimensionsCm": dict(zip("lwh", dims)), "deadline": now + due_h * 3600,
            "fragile": extra.get("fragile", False), "notes": extra.get("notes"),
            "priority": extra.get("priority", "standard"), "receiverEmail": extra.get("email"),
            "windowStart": now + extra["window_h"] * 3600 if extra.get("window_h") else None,
            "pickup": extra.get("pickup"),
            "destination": {"lat": lat, "lng": lng, "label": address},
        })
    print(f"Added {len(VEHICLES)} vehicles, {len(DRIVERS)} drivers, {len(PARCELS)} parcels.")


if __name__ == "__main__":
    main("--reset" in sys.argv)
