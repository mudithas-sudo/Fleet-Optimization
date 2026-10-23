# FedEx demo: setup and script (about 25 minutes)

The story follows how the operation is really set up and run: **register the fleet, run the day, then
watch it on the dashboard.** Each scene is shown by the person who does that job, using the sign-in
page's demo accounts.

## Setup (once, before you present)

1. In `.env`: your `MAPS_API_KEY` is set and `FAKE_ROUTES=0` (real roads). `GOOGLE_API_KEY` is optional:
   if the AI planner is unavailable, "Suggest runs" falls back to the built-in planner automatically.
2. Start an **empty** system (hub, three predefined routes and the demo staff accounts only):

   ```powershell
   cd "C:\Users\muditha.s\Delivery Management"
   .\samples\start-demo.bat -Fresh -Login
   ```

   - It deletes the old demo database (`fleet-demo.db`); your own `fleet.db` is never touched.
   - `-Fresh` = no vehicles, drivers, parcels or runs. `-Login` = sign-in on, with the demo accounts
     listed on the sign-in page (every password is `demo-pass-123`).
3. Open **http://localhost:8001** in Chrome and press **Ctrl+F5**. You should see the sign-in page with
   one card per user group.
4. Have these ready:
   - a second browser window for the **driver app** (you open it from a run in scene 2d),
   - the file `samples\manifest-demo.csv` (10 parcels) for the import,
   - the registration list below, on paper or a second screen.
5. Rehearse once the same way. To go again from scratch, press Ctrl+C and rerun the launcher.

### Registration list (type these live in Act 1)

| Vehicle | Plate | Type |
|---|---|---|
| Van 01 | CAB-4471 | Van |
| Van 02 | CAB-5120 | Van |
| Car 01 | KY-3308 | Car |
| Bike 01 | BKX-210 | Bike |

| Driver | Phone | Vehicle | Extra |
|---|---|---|---|
| Nuwan Perera | +94 77 123 4567 | Van 01 | |
| Kamal Silva | +94 71 555 0182 | Van 02 | |
| Dilani Fernando | +94 76 220 9914 | Car 01 | Shift 05:00 to 23:30 |
| Ruwan Jayasinghe | +94 70 441 7765 | Bike 01 | Max stops per run: 3 |

Choosing the vehicle **type** fills in sensible capacity, parcel size and cargo space, so each vehicle
is just name, plate and type.

## Act 1: Register the fleet (3 min)

**Sign in as Fleet manager** (`fleet1`, click "Use this account"). They land on **Fleet and drivers**,
and the menu is short: Fleet and drivers, Runs. There is no map on this page, because it isn't needed.

1. **Add** the four vehicles (name, plate, type). Point out that the type sets capacity and the
   largest parcel it can carry.
2. **Add** the four drivers: link each to a vehicle, set Dilani's shift, set Ruwan's max stops.
   Mention the other limits (max hours, on leave) that planning respects.
3. Open a driver and show the **driver link**: drivers need no login, they use their own link.

> Say: "Everything downstream (capacity, shifts, who can carry what) comes from this registry."

## Act 2: The operation (14 min)

**Sign out, then sign in as Dispatcher** (`dispatcher1`). Landing page is the dashboard (still empty);
go to **Parcels**.

### 2a. Bring in the day's parcels (3 min)
1. **Import** `samples\manifest-demo.csv`: the preview shows each row geocoded and any problems. Import.
   The page lists parcels as cards (status, progress line, receiver, call/email buttons).
2. **Add** one parcel by hand. Search a place, or use **Pin on map**: the map appears only now, because
   a place is being picked.
3. **Add > Pickup request**: a customer wants a collection. Customer address, cut-off time, collect-by time.
   It shows under **Pickup requests**.

### 2b. Plan the runs (3 min)
1. **Suggest runs**: the proposal fits parcels onto the fewest vehicles (capacity, size, driver
   shift/limits are checked) and flags anything that fits nowhere. Review, then **Approve**.
2. **Runs** page (the map is back, to show routes): open a run for the route, stops, ETAs and the
   briefing. Mention **Predefined routes** above the runs (R-01 to R-03): standing zones with a default
   driver and recurring pickups; **What's included**, then **Run today's route**, builds a run the same way.

### 2c. The warehouse hands over the parcels (2 min)
**Sign in as Warehouse / hub staff** (`warehouse1`). They land on **Warehouse > Loading bay**.
1. Choose a driver in **Show parcels for**: the list of parcels to hand to that driver, in **loading
   order** (last stop goes on first), with fragile and express flags.
2. Tick parcels as they are loaded (or **Mark all loaded**); **Print hand-over list** gives the paper copy.
3. Mention **Receiving**: pickups, undelivered parcels and returns are checked in here at the end of the day.

### 2d. The drivers go out (3 min)
Back as Dispatcher (or open the second window): in a run, copy the **driver link** and open it.
1. Start the trip, use the speed control; stops tick off as the van passes them.
2. Press **Deviate** once: an off-route alert, then an automatic re-route from the vehicle's position.
3. On a stop use **Couldn't deliver** (nobody home). On the pickup stop use **Collected**.

### 2e. Things that go wrong (3 min)
1. **Attention**: the failed delivery is waiting for review. Open it, **Review and reschedule** for
   tomorrow (nothing is returned or re-queued automatically).
2. A new pickup request arrives mid-day: add it, open it, **Best run for this pickup** (extra minutes,
   arrival time, cut-off check), **Add to run**: the live run is re-planned.
3. A vehicle breaks down: open the live run, **Change this run > Vehicle broke down**: preview where
   each remaining parcel would go, then **Reassign parcels**.

## Act 3: The dashboard (6 min)

**Sign in as Operations supervisor** (`supervisor1`). Landing page: **Dashboard**.

1. **Operations overview**: the five KPI cards (Late turns red only when something is late), the live
   map with the vehicles you just moved, the **Needs attention** list and **Pickup requests** beside
   it, and the rides table. Drag the time slider back to replay the day, then press **Live**.
2. **Impact** (side menu): km, hours, fuel, CO2 and cost saved against the same parcels in the order they
   were entered. Change the fuel price and watch it update. Plan-vs-actual and ETA accuracy fill in as
   runs finish.
3. **Reports**: cost per delivery, failures by reason, per-driver productivity, CSV export.
4. Customer view: open `/static/track.html?no=` with a tracking number from the Parcels page.
5. Close on the sign-in page: **eight groups, each with their own menu**, plus the driver app and the
   customer page, which need no account.

## Be straight about these

- The savings are **estimates** against a stated baseline (stops in the order entered). On a pilot they
  become real numbers from FedEx's own data.
- Fuel use comes from litres per 100 km by vehicle type (editable); it is not telematics.
- Data and addresses are made up (Colombo). Ask FedEx for their real service areas, cut-offs, vehicle
  mix and failed-delivery policy.

## A pre-loaded day instead (short version, about 15 min)

If you only have 15 minutes, load a finished-and-live sample day instead of an empty system:

```powershell
.\samples\start-demo.bat -Login -RealRoutes
```

It has 38 parcels, 5 runs (2 finished, 1 live, 2 planned), pickups, a failed delivery, and history for
the replay and Impact. Start at the sign-in page, show the warehouse loading bay and the dispatcher's
Attention list, then the dashboard and Impact. Load it **after 10:00**, because the day starts hours
before "now".

## If something goes wrong

| Problem | What to do |
|---|---|
| Map is grey or says "Oops" | Maps key or referrer problem: check `MAPS_API_KEY` in `.env`, then refresh. |
| "Suggest runs" fails | Read the message in the popup. The AI planner is optional (a built-in planner takes over). A "route came back ... away from the stops" message means a stop is on a bad address (for example inside a port): fix that address. |
| A parcel can't be imported | The preview shows why (address not found). Edit the CSV or add it by hand. |
| A page looks stale | Ctrl+F5. |
| Start over | Ctrl+C, then rerun the launcher. |
