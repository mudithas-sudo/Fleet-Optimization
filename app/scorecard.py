"""The research's KPI scorecard: the figures a courier's CFO asks for, each with the target the research gives.

Everything is computed from real parcels, runs and the cash ledger; a figure with no data yet is None.
"""

import time

from . import db, reports, risk


def _period(days: int | None) -> tuple[float, float]:
    return reports.window(days)


def summary(days: int | None = 7) -> dict:
    since, until = _period(days)
    rep = reports.summary(days)
    fa = risk.first_attempt(1 if days == 0 else days)
    parcels = [p for p in db.all_entities("parcels") if p.get("cod") and p["cod"]["state"] != "due"]
    taken = [p for p in parcels if since <= p["cod"].get("collectedAt", 0) <= until]
    collected = sum(p["cod"].get("collected", 0) for p in taken)
    short = sum(p["cod"].get("short", 0) for p in taken)
    deps = [d for d in db.all_entities("cod_deposits") if since <= d["createdAt"] <= until]
    lost = sum(-d["variance"] for d in deps if d["variance"] < 0)
    paid = [p for p in taken if p["cod"]["state"] == "remitted"]
    days_to_pay = (sum((p["cod"]["remittedAt"] - p["cod"]["collectedAt"]) for p in paid) / len(paid) / 86400) if paid else None
    held = [p for p in taken if p["cod"]["state"] == "deposited"]
    deliveries = sum(r["deliveries"] for r in rep["perDriver"])
    hours = sum(r["hours"] for r in rep["perDriver"])
    delivered = rep["parcels"]["delivered"]
    return {
        "firstAttemptPct": fa["firstAttemptPct"], "returnedPct": fa["returnedPct"],
        "onTimePct": rep["parcels"]["onTimeRate"],
        "confirmedFirstPct": fa["confirmed"]["firstAttemptPct"], "unconfirmedFirstPct": fa["unconfirmed"]["firstAttemptPct"],
        "kmPerParcel": round(rep["runs"]["km"] / delivered, 1) if delivered and rep["runs"]["km"] else None,
        "stopsPerHour": round(deliveries / hours, 1) if hours else None,
        "cod": {"collected": round(collected), "parcels": len(taken), "variancePct": round(100 * (short + lost) / collected, 2) if collected else None,
                "avgDaysToPay": round(days_to_pay, 1) if days_to_pay is not None else None,
                "waitingAtHub": round(sum(p["cod"].get("collected", 0) for p in held))},
        "failureReasons": fa["failureReasons"],
        "targets": {"firstAttemptPct": "90 to 94%", "returnedPct": "25 to 40% fewer", "onTimePct": "95% or more", "variancePct": "under 0.05%",
                    "avgDaysToPay": "2 days or less", "stopsPerHour": "10 to 20% more", "kmPerParcel": "8 to 18% less"},
        "source": "Targets are the pilot targets in the Sri Lanka courier research.",
    }
