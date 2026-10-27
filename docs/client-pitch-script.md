# Client presentation script (about 25 minutes)

The story, in the order you will tell it:

1. **Set up the fleet**: create vehicle records and assign drivers to them.
2. **Optimise**: show how parcels are grouped onto vehicles and ordered by the distance between destinations.
3. **The real-world pain points** this solution addresses: cash on delivery, failed first attempts, disruptions.
4. **The dashboard**: everything in one view, and the proof of value.

Each part is one idea, with what to click and a line to say. For the full test-style walkthrough see [demo-script.md](demo-script.md).

## Before you start

```powershell
cd "C:\Users\muditha.s\Delivery Management"
.\samples\start-demo.bat -Login -RealRoutes
```

Open http://localhost:8001, press Ctrl+F5, and load it **after 10:00** so the live runs look right. Every password is `demo-pass-123`.

Why this and not the empty start: parts 3 and 4 need history (cash held by riders, a refuser, a week of runs). The sample day already has 9 vehicles and
drivers, so in part 1 you **add two more live**, and those two new drivers are the free ones that part 2 plans onto.

Have ready:
- a second browser window for the customer page: `http://localhost:8001/static/track.html?no=FO-300119`;
- the file `samples\manifest-demo.csv` (10 parcels spread across Colombo, deliberately in a zig-zag order);
- the two new records to type:

| Vehicle | Plate | Type |
|---|---|---|
| Van 05 | CAB-8821 | Van |
| Bike 03 | BKX-455 | Bike |

| Driver | Phone | Vehicle |
|---|---|---|
| Asela Gunasekara | +94 77 300 4411 | Van 05 |
| Priyanka Jayawardena | +94 71 880 3302 | Bike 03 |

## Opening (1 min), say this

> "Most delivery problems are not driving problems. They are a customer who was not home, cash that went missing, a flooded road nobody told the rider
> about. We start with the basics, a fleet and its drivers, and I will show you how it plans, and then the problems that actually cost you money."

Show the sign-in page: five staff accounts, each with their own menu, plus the driver app and the customer page that need no account.

---

## Part 1: Create vehicles and assign drivers (4 min), sign in as `supervisor1`

Open **Fleet and drivers**. There is no map on this page, because it is not needed.

1. **Add vehicle**: Van 05, CAB-8821, type **Van**. Point out that choosing the type fills in the load capacity, the largest parcel it can carry and the cargo space.
   Add **Bike 03**, BKX-455, type **Bike**: the capacity is much smaller, automatically.
2. **Add driver**: Asela Gunasekara, phone, **vehicle: Van 05**. This is the assignment: a driver belongs to a vehicle, so planning knows who carries what.
3. **Add driver**: Priyanka Jayawardena on **Bike 03**. Open **Advanced** and set **Max stops per run: 3** (and a shift if you want).
4. Open a driver and show the **driver link**: drivers need no login, the link is their app.

> Say: "Everything the planner knows, capacity, parcel size, shift hours, stop limits, comes from this registry. A bike will never be given a sofa,
> and a driver will never be given more stops than agreed."

Mention in passing: the fleet manager role exists for a dedicated fleet team, and supervisors and administrators can do the same.

---

## Part 2: How the fleet is optimised (6 min), sign out, sign in as `dispatcher1`

### 2a. Bring the day's parcels in (1 min)
**Parcels > Import** `samples\manifest-demo.csv`. The preview shows each address found on the map and flags any problem row. Import it.

> Say: "Ten parcels, entered in the order the customers ordered. Look at the places: Bambalapitiya, then Nugegoda, then Rajagiriya, then Borella. Driving
> them in this order would zig-zag across the city."

### 2b. Let the system plan (3 min)
1. **Suggest runs**. It fits parcels onto the fewest vehicles using capacity, parcel size and each driver's shift and limits. Express goes first; anything that
   fits nowhere is flagged (the crated industrial press). Notice **Asela on Van 05** and **Priyanka on Bike 03** appear: they are the free drivers you just created.
2. Read out one line from the proposal: parcels per vehicle, total distance, and any parcel that could not be placed. **Approve.**
3. **Runs**: open one of the new runs. The map shows the **stop order the system chose**, with distance and arrival time at each stop. Compare it with the order in the
   import list: nearby stops are grouped, the route flows in one direction, and the hub is the start and the end.

> Say: "The system decides two things: which vehicle takes which parcels, and in what order to visit them, based on the actual road distance
> between destinations, not a straight line. Express parcels and delivery windows are respected, and a customer pickup is always collected before it is dropped at the hub."

### 2c. Keep it optimal when plans change (2 min)
- A new **pickup request** arrives: open it, **Best run for this pickup** shows the extra minutes it adds to each run and whether the customer's cut-off is met. **Add to run**
  re-plans a run that is already out.
- If a vehicle drives off the planned road, the system raises an alert and **re-routes from where the vehicle actually is** (use the driver app's Deviate button if you want to show it).

### 2d. Show the saving (in part 4, on the Impact page)
Do not stop here to explain figures. Say: "I will show you what this is worth in money at the end."

---

## Part 3: The additional pain points we address (9 min)

> Say: "The route is only half of the problem. These are the other things that cost you money every day."

### 3a. Cash on delivery (3 min), `warehouse1`, then `supervisor1`
Open **Cash**.
- **Riders holding cash**: Van 03 is over the cash limit (red); another rider has not handed in yesterday's cash (amber).
- **Count cash in** for one rider and type a slightly lower amount: the difference is recorded against the rider and the deposit slip.
- As `supervisor1`, **Owed to merchants**: how many days each seller has waited, then **Mark paid** with a bank reference.
- On the parcel itself the rider's app shows **Collect LKR 21,500 cash** and will not mark the parcel delivered until it is taken.

> Say: "A delivery is not complete until the cash is in the rider's hand, and every rupee is followed to the merchant's account. This is what moves a seller's
> payout from weeks to days."

### 3b. Stop the failed first attempt before it happens (4 min), `dispatcher1`
1. **Attention > Confirm before dispatch**: the riskiest parcels first, each with a stated reason.
2. Open **FO-300119**: **Delivery risk** lists the points (cash on delivery, this number refused a parcel before, not confirmed). Nothing is a black box.
3. **Send confirmation message**. Switch to the **customer window**: the receiver sees "Will you be there?", the cash to keep ready, a time window, a directions box,
   and **Yes, I will be there** or **another day**. Press yes.
4. Back in the console the score drops and the parcel leaves the call list.
5. Open the parcel for 071 340 9902: **Use the pin from past deliveries**. The place where the last delivery actually succeeded becomes the address.

> Say: "The customer answers in ten seconds, the rider arrives knowing someone is home and where the door is, and the cash is ready."

### 3c. When the real world interferes (2 min), **Attention**
- **Area holds**: type a town, **Hold this area**. Parcels there come off planned runs, stay out of Suggest runs, and customers are told why. **Reopen the area** afterwards.
- **A rider isn't coming in**: their planned run drops back into the queue.
- **Vehicle broke down**: open the live run, **Change this run > Vehicle broke down**, preview where each remaining parcel goes, then apply.
- **Capacity for the next days**: parcels against riders, the extra riders to book when a day is short, and the weekly fuel allowance per vehicle.

> Say: "Floods, hartals, fuel limits and absent riders are normal days here. The system plans around them instead of being surprised by them."

---

## Part 4: The dashboard and the proof (4 min), sign in as `supervisor1`

Landing page: **Dashboard**.

1. **Operations overview**: the five KPI cards (Late turns red only when something is late), the live map with the vehicles you just planned and the live run,
   and **Needs attention** beside it: riders over the cash limit, high-risk parcels not confirmed, failed deliveries waiting for review. Drag the time slider back
   to replay the day, then press **Live**.
2. **Impact** (side menu): kilometres, hours, fuel, CO₂ and cost saved against the same parcels visited in the order they were entered. This is the answer to the
   zig-zag in part 2. Below it, the **scorecard**: first-attempt rate, returns, on-time, days to pay merchants, cash variance, stops per rider-hour and distance per
   parcel, each next to the target from the market research.
3. **Reports**: on-time rate, first-attempt rate, why deliveries fail, deliveries per day.
4. The **customer view** (`track.html`) and the **driver app** need no account.

> Closing line: "Fleet, planning, cash, confirmation and disruptions in one console, with a role for each person. The next step is a four-week pilot on one branch with your
> real parcels, so the numbers you just saw become yours."

---

## Questions you will get

| Question | Honest answer |
|---|---|
| Are the savings real? | They are estimates against a stated baseline (stops in the order entered). On a pilot they become measured figures from your data. |
| How does it choose the order? | By road distance and time between stops, with express parcels, delivery windows and pickup-before-drop rules applied first. A Gemini planner is available, and a built-in planner takes over when it is unavailable. |
| Does it message customers on WhatsApp? | Messages are recorded and sent through a webhook; connecting a WhatsApp or SMS provider is a configuration step. |
| Is the risk score AI? | It is a transparent rule set; each point has a reason. It can be replaced by a model trained on your own history. |
| Does it move money? | No. It is the ledger: what was collected, counted and owed. Payment integration is a later step. |
| Capacity and fuel figures? | Estimates from vehicle type and rider limits; replaced by your real numbers in a pilot. |
| Can it use our systems? | It imports a CSV manifest now; order-system integration is the pilot's first integration. |
| What data is this? | Made-up Colombo data. Ask for your service areas, cut-offs, vehicle mix and failed-delivery policy. |

## If something goes wrong on the day

| Problem | Do this |
|---|---|
| Page looks old | Ctrl+F5. |
| Map is grey | Maps key or referrer problem: check `MAPS_API_KEY` in `.env`, refresh. |
| Import row fails | The preview says why (address not found): fix the CSV or add the parcel by hand. |
| "Suggest runs" fails | Read the message in the popup; the AI planner is optional, a built-in planner takes over. |
| Data looks stale or you want to start over | Ctrl+C, rerun `.\samples\start-demo.bat -Login -RealRoutes`. |
