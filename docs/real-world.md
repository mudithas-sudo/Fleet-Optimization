# Sri Lankan last-mile pain points: what the prototype now does about them

Source: the research report "AI-Automated Courier Scheduling in Sri Lanka: Scenario Playbook, Architecture and ROI Case".
Its own advice was to fund pre-delivery confirmation and address pinning first, then dynamic recovery. This is how each of those maps onto the
product. The report is about the **domestic e-commerce courier** market, so the parts that do not fit FedEx's international express operation
(cash on delivery, merchant payouts, fuel quotas) were built, then removed: see [fedex-sri-lanka.md](fedex-sri-lanka.md).

| Pain point in the research | What the product does now | Where |
|---|---|---|
| Failed first attempts (no one home, refused, vague address) are the biggest cost | Every open parcel has an explainable risk score. The call list puts the riskiest first, and the receiver can answer from the tracking page (confirm and say when, add directions, pick another day, cancel) | Attention > Confirm before dispatch; tracking page; `app/risk.py` |
| Serial refusers | A phone number's past refusals and no-shows raise the score of its next parcel | Delivery risk panel |
| Addresses are landmarks, not streets | A landmark field travels to the rider; the position of a successful drop-off is remembered per phone number and offered as a better pin | Add parcel > More; Delivery risk > Use the pin |
| No phone number, so the rider cannot call | Raises the risk score and is shown as the reason | Delivery risk |
| Peak days |  A forecast of demand against available riders, with the extra capacity to ask the contractor for | Attention > Capacity |
| Floods, hartals, road closures | An area hold keeps parcels out of every planner, takes them off planned runs and tells the customer why; live runs heading there are reported so the rider can be called | Attention > Area holds |
| A rider does not turn up | One action returns their planned runs to the queue for re-planning | Attention > A rider isn't coming in |
| Nobody measures first-attempt success | First-attempt delivery rate on Reports, split by confirmed and not confirmed, with failure reasons | Reports |

## What is deliberately not here yet

- **Customer messages go to a webhook, not WhatsApp itself.** They are recorded and sent through `NOTIFY_WEBHOOK_URL`; a WhatsApp or SMS
  provider is a configuration step in a pilot.
- **The risk score is a rule set with visible points, not a trained model.** It is built so each point can be argued with, and it can be
  replaced by a model trained on the customer's own history.
- **Capacity figures are estimates** (stops per day by vehicle type and each rider's stop limit). A pilot should replace them with the real figures.
- **Extra capacity** is shown only as a number to ask the service contractor for.
- **Live runs in a newly held area are reported, not re-planned automatically**, because a rider already on the road is a call, not a calculation.

## How to try it

`.\samples\start-demo.bat -Login` loads the sample day with the cases above already in it. The walkthrough is
**Act 4** of [demo-script.md](demo-script.md). Tests: `venv/bin/python tests/test_realworld.py` (offline, no keys).
