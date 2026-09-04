"""Regenerate the sample parcel files with fresh deadlines relative to now.

Run before a demo so deadlines land in the future:
    venv/bin/python samples/make_samples.py
"""

import csv
from datetime import datetime, timedelta
from pathlib import Path

from openpyxl import Workbook

HERE = Path(__file__).parent
NOW = datetime.now()

def dl(hours):
    return (NOW + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")

HEADER = ["name", "type", "size", "destination", "deadline"]

# a demo-sized set: clustered smalls with tight deadlines, loose larges,
# one urgent outlier, one oversize, and one broken row (validation demo)
DEMO = [
    ["Router #4411", "electronics", "small", "Majestic City Colombo", dl(1.5)],
    ["SIM starter kit", "electronics", "small", "Nugegoda Junction", dl(1.6)],
    ["Phone case bundle", "accessories", "small", "Maharagama", dl(2)],
    ["Legal documents", "papers", "small", "Malabe SLIIT", dl(0.4)],
    ["Washing machine", "appliance", "large", "Kadawatha", dl(7)],
    ["Queen mattress", "furniture", "large", "Kiribathgoda", dl(8)],
    ["Birthday cake", "food", "medium", "Mount Lavinia", dl(2.5)],
    ["Broken row", "mystery", "huge", "xyzzy nowhere 999", "not-a-date"],
]

# a bigger spread for the stress/auto-assign demo
STRESS = [
    ["Laptop repair kit", "electronics", "small", "Borella", dl(1.2)],
    ["Insulin pack", "medical", "small", "Colombo National Hospital", dl(0.8)],
    ["Office chair", "furniture", "large", "Battaramulla", dl(6)],
    ["Filing cabinet", "furniture", "large", "Rajagiriya", dl(6.5)],
    ["School books", "papers", "medium", "Dehiwala", dl(3)],
    ["Textile rolls", "goods", "large", "Wattala", dl(7)],
    ["Spice hamper", "food", "small", "Pettah Market", dl(1.8)],
    ["Wedding invitations", "papers", "small", "Kollupitiya", dl(2.2)],
    ["Router #5520", "electronics", "small", "Moratuwa University", dl(4)],
    ["Gaming console", "electronics", "medium", "Nugegoda", dl(3.5)],
    ["Standing fan", "appliance", "medium", "Kelaniya", dl(5)],
    ["Cricket gear", "sports", "medium", "Havelock City Mall", dl(4.5)],
    ["Fish cooler box", "food", "medium", "Negombo Road Wattala", dl(2.8)],
    ["Curtain set", "goods", "small", "Kotahena", dl(5.5)],
    ["Server rack parts", "electronics", "large", "Malabe", dl(6)],
]

def write_xlsx(path, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "parcels"
    ws.append(HEADER)
    for r in rows:
        ws.append(r)
    wb.save(path)

def write_csv(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)

write_xlsx(HERE / "parcels-demo.xlsx", DEMO)
write_xlsx(HERE / "parcels-stress.xlsx", STRESS)
write_csv(HERE / "parcels-demo.csv", DEMO)
print("wrote parcels-demo.xlsx (%d rows), parcels-stress.xlsx (%d rows), parcels-demo.csv"
      % (len(DEMO), len(STRESS)))
