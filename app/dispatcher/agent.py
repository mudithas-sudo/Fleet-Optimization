"""Dispatch agent — proposes how to batch pending parcels into delivery runs.

The genuinely agentic piece: judgment over eligibility, deadlines and
geography, verified against real routes, explained in dispatcher language.
The proposal is validated server-side and only executed after admin approval.
"""

import os

from google.adk import Agent
from pydantic import BaseModel, Field

from .tools import evaluate_route, list_fleet_availability, list_pending_parcels


class Batch(BaseModel):
    driverId: str
    parcelIds: list[str]
    rationale: str = Field(description="1-2 sentences a dispatcher would accept: why these parcels, this driver, this grouping.")
    riskNotes: str = Field(default="", description="Deadline or capacity concerns worth flagging; empty string if none.")


class Unassigned(BaseModel):
    parcelId: str
    reason: str


class DispatchPlan(BaseModel):
    batches: list[Batch]
    unassigned: list[Unassigned]
    summary: str = Field(description="2-3 sentence overview of the whole plan for the dispatcher.")


dispatch_agent = Agent(
    name="dispatch_planner",
    model=os.environ.get("DISPATCH_MODEL") or os.environ.get("MODEL", "gemini-3.5-flash-lite"),
    description="Batches pending parcels into delivery runs across the fleet.",
    instruction=(
        "You are a delivery dispatch planner. Your job: group the pending "
        "parcels into delivery runs and assign each run to a driver.\n"
        "Process:\n"
        "1. Call list_pending_parcels and list_fleet_availability first.\n"
        "2. Form candidate batches: group parcels that are geographically "
        "close (compare lat/lng) and compatible in deadline pressure. Respect "
        "each vehicle's maxParcelSize and capacity. Urgent parcels (small "
        "deadlineInMinutes) go out first, on idle drivers. Some parcels have a "
        "`pickup` {label,lat,lng}: that parcel must be collected there before "
        "delivery, so its run visits two locations — favour batching parcels "
        "whose pickups are near each other or near the deliveries.\n"
        "3. VERIFY every batch you intend to propose with evaluate_route "
        "before including it. Its `perStop` ETAs (one row per collect/deliver, "
        "with kind) are the only valid timing source - never estimate travel "
        "times yourself. If a stop comes back atRisk, try a different grouping "
        "or driver; if nothing works, still assign it but explain the risk in "
        "riskNotes, or leave it unassigned with the reason if hopeless.\n"
        "4. Parcels no vehicle can take (size/capacity) go to unassigned "
        "with the reason.\n"
        "Prefer fewer, fuller runs when deadlines allow. A driver can receive "
        "at most one batch in this plan. Rationales are 1-2 concrete "
        "sentences (mention area, deadlines, vehicle fit) - no filler."
    ),
    tools=[list_pending_parcels, list_fleet_availability, evaluate_route],
    output_schema=DispatchPlan,
    output_key="dispatch_plan",
)
