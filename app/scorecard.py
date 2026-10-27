"""The research's KPI scorecard: the figures an express operator's finance team asks for, each with the target the research gives.

Everything is computed from real parcels and runs; a figure with no data yet is None.
"""

from . import reports, risk


def summary(days: int | None = 7) -> dict:
    rep = reports.summary(days)
    fa = risk.first_attempt(1 if days == 0 else days)
    deliveries = sum(r["deliveries"] for r in rep["perDriver"])
    hours = sum(r["hours"] for r in rep["perDriver"])
    delivered = rep["parcels"]["delivered"]
    return {
        "firstAttemptPct": fa["firstAttemptPct"], "returnedPct": fa["returnedPct"],
        "onTimePct": rep["parcels"]["onTimeRate"],
        "confirmedFirstPct": fa["confirmed"]["firstAttemptPct"], "unconfirmedFirstPct": fa["unconfirmed"]["firstAttemptPct"],
        "kmPerParcel": round(rep["runs"]["km"] / delivered, 1) if delivered and rep["runs"]["km"] else None,
        "stopsPerHour": round(deliveries / hours, 1) if hours else None,
        "failureReasons": fa["failureReasons"],
        "targets": {"firstAttemptPct": "90 to 94%", "returnedPct": "25 to 40% fewer", "onTimePct": "95% or more",
                    "stopsPerHour": "10 to 20% more", "kmPerParcel": "8 to 18% less"},
        "source": "Targets are the pilot targets in the Sri Lanka courier research.",
    }
