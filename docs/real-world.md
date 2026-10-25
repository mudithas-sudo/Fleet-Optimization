# Sri Lankan last-mile pain points: what the prototype now does about them

Source: the research report "AI-Automated Courier Scheduling in Sri Lanka: Scenario Playbook, Architecture and ROI Case".
Its own advice was to fund pre-delivery confirmation, address pinning and digital cash on delivery first, then dynamic
recovery. This is how each of those maps onto the product.

| Pain point in the research | What the product does now | Where |
|---|---|---|
| Most parcels are cash on delivery; cash is lost, late or short | A parcel carries the cash to collect and the seller it is owed to. The rider must take the cash to complete the delivery. Cash is followed from rider to hub to merchant, with a carry limit, a hand-in time and every counting difference recorded | Cash page; `app/cod.py` |
| Merchants chase for their money | Per merchant: with riders, at the hub, how many days it has waited, paid out (with the bank reference) | Cash > Owed to merchants |
| Failed first attempts (no one home, refused, vague address) are the biggest cost | Every open parcel has an explainable risk score. The call list puts the riskiest first, and the receiver can answer from the tracking page (confirm and say when, add directions, pick another day, cancel) | Attention > Confirm before dispatch; tracking page; `app/risk.py` |
| Serial refusers of cash on delivery | A phone number's past refusals and no-shows raise the score of its next parcel | Delivery risk panel |
| Addresses are landmarks, not streets | A landmark field travels to the rider; the position of a successful drop-off is remembered per phone number and offered as a better pin | Add parcel > More; Delivery risk > Use the pin |
| No phone number, so the rider cannot call | Raises the risk score and is shown as the reason | Delivery risk |
| Floods, hartals, road closures | An area hold keeps parcels out of every planner, takes them off planned runs and tells the customer why; live runs heading there are reported so the rider can be called | Attention > Area holds |
| A rider does not turn up | One action returns their planned runs to the queue for re-planning | Attention > A rider isn't coming in |
| Peak days and fuel rationing | A forecast of demand against available riders (with the extra riders to book), and a weekly fuel allowance per vehicle showing litres used and left | Attention > Capacity |
| Nobody measures first-attempt success | First-attempt delivery rate on Reports, split by confirmed, not confirmed and cash on delivery, with failure reasons | Reports |

## What is deliberately not here yet

- **Customer messages go to a webhook, not WhatsApp itself.** They are recorded and sent through `NOTIFY_WEBHOOK_URL`; a WhatsApp or SMS
  provider is a configuration step in a pilot.
- **The risk score is a rule set with visible points, not a trained model.** It is built so each point can be argued with, and it can be
  replaced by a model trained on the customer's own history.
- **Capacity and fuel figures are estimates** (stops per day by vehicle type; litres from the Impact fuel settings). A pilot should replace them
  with the real figures.
- **Cash counting is a ledger, not a payment integration.** It records what was counted and paid out; it does not move money.
- **Gig or partner riders** are shown only as "extra riders to book". Onboarding and paying them is out of scope.
- **Live runs in a newly held area are reported, not re-planned automatically**, because a rider already on the road is a call, not a calculation.

## How to try it

`.\samples\start-demo.bat -Login` loads the sample day with the cases above already in it. The walkthrough is
**Act 4** of [demo-script.md](demo-script.md). Tests: `venv/bin/python tests/test_realworld.py` (offline, no keys).
