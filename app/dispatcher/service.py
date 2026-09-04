"""Programmatic invocation of the dispatch agent + server-side validation.

The LLM's proposal is untrusted: hallucinated ids, double-assigned parcels and
eligibility violations are stripped here, deterministically, before the admin
ever sees the plan.
"""

import uuid

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .. import db, delivery
from .agent import dispatch_agent

APP_NAME = "fleet-dispatch"
USER_ID = "admin"

_session_service = InMemorySessionService()
_runner = Runner(app_name=APP_NAME, agent=dispatch_agent,
                 session_service=_session_service)


async def propose_dispatch(parcel_ids: list[str] | None = None) -> dict:
    session_id = f"dispatch-{uuid.uuid4().hex[:8]}"
    await _session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=session_id,
        state={"scope_parcel_ids": parcel_ids})

    message = types.Content(role="user", parts=[types.Part(
        text="Plan delivery runs for the current pending parcels.")])

    async for _event in _runner.run_async(
            user_id=USER_ID, session_id=session_id, new_message=message):
        pass

    session = await _session_service.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=session_id)
    plan = session.state.get("dispatch_plan")
    if not isinstance(plan, dict):
        raise RuntimeError("The dispatch agent produced no plan.")

    return _validate(plan, parcel_ids)


def _validate(plan: dict, scope: list[str] | None) -> dict:
    warnings: list[str] = []
    seen_parcels: set[str] = set()
    seen_drivers: set[str] = set()
    batches = []
    unassigned = list(plan.get("unassigned") or [])

    for b in plan.get("batches") or []:
        driver = db.load_entity("drivers", b.get("driverId") or "")
        vehicle = driver and db.load_entity("vehicles", driver.get("vehicleId") or "")
        if not driver or not vehicle:
            warnings.append(f"Dropped a batch with unknown driver '{b.get('driverId')}'.")
            continue
        if driver["id"] in seen_drivers:
            warnings.append(f"Dropped a duplicate batch for {driver['name']}.")
            continue

        parcels = []
        for pid in b.get("parcelIds") or []:
            p = db.load_entity("parcels", pid)
            if not p or p["status"] != "pending" or (scope and pid not in scope):
                warnings.append(f"Removed unavailable parcel '{pid}' from a batch.")
                continue
            if pid in seen_parcels:
                warnings.append(f"Parcel '{p['name']}' appeared twice; kept the first batch.")
                continue
            parcels.append(p)
        if not parcels:
            continue

        reason = delivery.check_eligibility(vehicle, parcels)
        if reason:
            warnings.append(
                f"Batch for {driver['name']} failed eligibility ({reason}); parcels left unassigned.")
            unassigned.extend({"parcelId": p["id"], "reason": reason} for p in parcels)
            continue

        seen_drivers.add(driver["id"])
        seen_parcels.update(p["id"] for p in parcels)
        batches.append({
            "driverId": driver["id"],
            "driverName": driver["name"],
            "vehicleType": vehicle["type"],
            "vehicleName": vehicle["name"],
            "vehiclePlate": vehicle.get("plate", ""),
            "parcelIds": [p["id"] for p in parcels],
            "parcelNames": [p["name"] for p in parcels],
            "rationale": b.get("rationale", ""),
            "riskNotes": b.get("riskNotes", ""),
        })

    # enrich unassigned with names; drop ids that don't exist
    clean_unassigned = []
    for u in unassigned:
        p = db.load_entity("parcels", u.get("parcelId") or "")
        if p:
            clean_unassigned.append({
                "parcelId": p["id"], "name": p["name"],
                "reason": u.get("reason", ""),
            })

    if not batches and not clean_unassigned:
        raise RuntimeError("The dispatch agent could not produce any valid assignment.")

    return {
        "batches": batches,
        "unassigned": clean_unassigned,
        "summary": plan.get("summary", ""),
        "warnings": warnings,
    }
