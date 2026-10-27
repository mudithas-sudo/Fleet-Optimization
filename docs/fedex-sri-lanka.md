# How FedEx operates in Sri Lanka, and what the prototype does about it

Public information about FedEx's Sri Lankan operation is thin. This page separates what the sources actually say from what is assumed, and
lists the changes made to the prototype. Confirm the assumed items with FedEx before relying on them.

## What the sources say

| Finding | Source |
|---|---|
| FedEx is represented in Sri Lanka by a **nominated Service Contractor** (listed as Advantis Express on FedEx's support page), with a Colombo phone line for import and export enquiries. | [FedEx Sri Lanka customer support](https://www.fedex.com/en-lk/customer-support.html) |
| FedEx lists **two Stations** in Sri Lanka: Colombo and Dehiwala (Galle Road). Opening hours are not published on the locator. | [FedEx locations, Sri Lanka](https://local.fedex.com/en-lk), [Colombo station](https://local.fedex.com/en-lk/colombo/cmba) |
| International shipments are **cleared through Sri Lanka Customs** before delivery. Customs runs a published courier route with a fast-clearance path and a **detention path**. | [Customs: expedited shipments](https://www.customs.gov.lk/wp-content/uploads/2021/06/Expedited-Shipments-PDF.pdf), [Customs FAQ](https://www.customs.gov.lk/personal/faq/) |
| When Customs detains a parcel, the courier notifies the receiver, who must **attend an examination** and pay any duty and tax. | [Customs FAQ](https://www.customs.gov.lk/personal/faq/), [online buyers](https://www.customs.gov.lk/personal/online-buyers/) |
| Customs collects duty **only at designated points** (airports, post offices, courier customs points, bonded warehouses) and warns that any request to send money to a private account is a likely scam. | [Customs FAQ](https://www.customs.gov.lk/personal/faq/) |
| In July 2025 the President ordered Customs to speed up e-commerce parcel clearance after a backlog, and to explore courier pre-payment. Whether pre-payment is in force is unconfirmed. | [News report](https://www.newsonair.gov.in/sri-lankan-president-orders-urgent-customs-reforms-to-clear-e-commerce-parcel-backlog) |

Not found in any reliable source: FedEx's Sri Lankan pickup cut-off times, delivery commit times, fleet size, delivery-driver model, or whether
FedEx Delivery Manager features are offered locally. Third-party blogs make claims about these, and they are not used here.

## What this means for the operation (inference)

- Most FedEx volume in Sri Lanka is **international express**, so a large share of deliveries are **imports that must clear Customs first**,
  then a time-sensitive last mile by a service contractor. Exports are pickups from customers.
- A parcel stuck at Customs is not a delivery problem, but it looks like one to the receiver. They need to be told, and told what to do.
- Money for duty and tax goes to Customs, not to the rider, which is why this prototype does **not** collect duty at the door.

## What changed in the prototype

| Area | Change |
|---|---|
| Parcel | **Shipment**: domestic or **international import** (with where it arrived from). An import starts as awaiting Customs clearance. |
| Planning | A parcel still with Customs is **not plannable**: it never appears in Suggest runs, predefined routes or the add-to-run lists, and is taken off a planned run if Customs holds it. The existing express and priority handling is unchanged. |
| Attention page | **Held at Customs** list: mark a parcel as held (receiver must attend) or cleared, newest and most urgent first. |
| Customer messages | A **Customs hold** message telling the receiver to attend the examination and pay at the Customs point, never to a person or private account; a **cleared** message when it is released. |
| Tracking page | Shows "Awaiting customs clearance" or "Held by Customs: receiver must attend an examination" instead of a delivery status. |
| Parcel list | `in customs`, `customs hold` and `import` chips. |
| Removed |  Cash on delivery (cash page, ledger, merchant payouts, rider cash limits, sellers report) and the fuel allowance. They come from the domestic e-commerce courier research and do not apply to FedEx international express, which is mostly prepaid. |
| Sample data | Five imports (two clearing, two held for examination, one cleared, express and priority among them). |

There is still **no domestic or international split in the optimiser**: everything is one pool; Customs only decides when a parcel becomes plannable.

## Suggestions that need FedEx's input

1. **Stations as drop-off and collection points.** FedEx has Colombo and Dehiwala stations. "Collect from the station" after a failed attempt would suit receivers who are never home.
2. **Time-definite commit times** per service (the prototype has express, priority and standard with a deadline per parcel; FedEx's real commitments are not public).
3. **Service contractor view**: if the contractor runs the vehicles, the Fleet page is theirs, and FedEx sees only the Dashboard and Reports.
4. **Customs integration.** Clearance status is entered by hand. An integration with the courier's customs system would update it automatically.
