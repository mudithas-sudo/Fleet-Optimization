# FedEx demo: setup and script (about 20 minutes)

## Before the demo

1. **Start fresh data, right before you present** (all sample times are relative to "now"):

   ```powershell
   cd "C:\Users\muditha.s\Delivery Management"
   .\samples\start-demo.bat -RealRoutes -Users
   ```

   - This deletes the old demo database (`fleet-demo.db`) and loads a new working day. Your own
     `fleet.db` is never touched.
   - `-RealRoutes` draws the sample runs along real roads (a handful of Maps calls with your key).
     Leave it out to use offline straight-line routes (no calls, but the sample runs look straight).
   - `-Users` adds demo staff accounts (only used if you set `AUTH_ENABLED=1`).
2. Open **http://localhost:8001** in Chrome and press **Ctrl+F5**. The map must show Colombo tiles.
3. Keep a second tab open for the driver app (you get the link from a run, step 4).
4. To rehearse again without resetting: `.\samples\start-demo.bat -Keep`.

What the data contains: 38 parcels, 4 runs (2 finished, 1 live, 1 scheduled), 3 pickup requests,
1 failed delivery waiting for review, 3 predefined routes (R-01, R-02, R-03), 5 vehicles and
5 drivers, customer messages, and a position trail for the replay.

## The story

**1. Operations overview (2 min).** The dashboard answers "how is today going?" in seconds: five
KPI cards (Late is red only when something is late), the live map, the attention list on the right,
and the rides table. Drag the time slider back to replay the day, then click **Live**.

**2. Needs attention (2 min).** Open "Failed delivery — review" (the gift hamper). Show the reason
(nobody home), then **Review and reschedule** with tomorrow's date. Point out that nothing is
returned or re-queued automatically, and the customer gets a message.

**3. Plan the day (3 min).** Parcels page, then **Suggest runs**. It fits pending parcels onto the
fewest vehicles (capacity, size, driver shift and leave are checked; the oversized pallet is flagged
as not carriable). Approve a run. Open it to show the route, stops and ETAs.

**4. Driving (3 min).** In the run, copy the driver link and open it in the second tab. Start the
trip and use the speed control; stops tick off automatically. Press **Deviate** once: the dashboard
raises an off-route alert and the route is re-planned from the vehicle's position.

**5. Pickup arrives mid-day (2 min).** Add parcel, choose **Pickup request**, set the customer address
and cut-off. Open it and use **Best run for this pickup**: it shows extra minutes, arrival time and
the cut-off check. Click **Add to run**; the live run is re-planned. The driver taps **Collected**
and the shipment comes back to the hub as the last stop.

**6. Vehicle breakdown (2 min).** Open the live run, then **Change this run**, then **Vehicle broke
down**. The preview shows where each remaining parcel would go; **Reassign parcels** hands them over
and re-plans the receiving runs.

**7. Predefined routes (2 min).** Runs page: R-01 / R-02 / R-03 are standing routes with a zone, a
default driver and recurring pickups. Use **What's included**, then **Run today's route**.

**8. Impact (3 min).** Click **Impact**. Show km, hours, fuel, CO₂ and cost saved against the same
parcels in the order they were entered, plan vs actual, and ETA accuracy. Change the fuel price or
litres per 100 km and watch the figures update.

**9. Close (1 min).** Reports (cost per delivery, CSV export), the customer tracking page (open
`/static/track.html?no=FO-100114`), and roles if you enable sign-in.

## Be straight about these

- The savings are **estimates** against a defined baseline (stops in entry order). They become real
  numbers on a pilot with FedEx's own data.
- Fuel use comes from litres per 100 km by vehicle type (editable). It is not telematics.
- Sample parcels, drivers and routes are made up (Colombo).
- Ask FedEx for their real rules: service areas, cut-offs, vehicle mix, return and review policies.

## If something goes wrong

| Problem | What to do |
|---|---|
| Map is grey or says "Oops" | Maps key or referrer problem; check the Maps key in `.env`, then refresh. |
| "Suggest runs" or approve is slow or fails | The AI planner is optional: runs fall back to the built-in planner automatically. |
| A run looks wrong | Reload with `.\samples\start-demo.bat` (fresh data). |
| Page looks stale | Ctrl+F5. |
| Need to start over | Stop the window (Ctrl+C) and run the launcher again. |
