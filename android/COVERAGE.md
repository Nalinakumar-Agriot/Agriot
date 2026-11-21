# Coverage and acceptance status

Status key: **Implemented** means client code exists; it does not mean live end-to-end testing passed.
Build/unit/lint/emulator results are recorded by GitHub Actions, never inferred from source review.

| Area | Client status | Remaining acceptance |
|---|---|---|
| OTP login / registration | Implemented | Real SMS, wrong/expired/reused OTP, network loss, deployed parser parity |
| Farmer / manager profiles | Implemented | Multi-workspace scoping and authorization; backend only accepts profile type for switching |
| Subscription module access | Implemented, fail closed | Expiry, suspended account, package change, server authorization for all routes |
| Dashboard | Implemented owner aggregates | Compare ledger/inventory totals; manager aggregates withheld because source is owner-wide |
| Farm CRUD | Implemented | Farm/acre limits, concurrent allocation, relation preservation |
| Crop setup / manager assignment | Implemented | Tenant scope, acreage races, full master data |
| Crop stages / first harvest / sale | Implemented | Units, stock deductions, grade matching, customer ledger reconciliation |
| Multiple harvest batches / next flush | Partial | Next-flush control implemented; multiple grade rows remain |
| Task list / dates / assignment | Implemented | Generated task dates and calendar parity; no month-grid calendar yet |
| Consumption / inventory | Implemented | Atomic stock decrement and audit log; editing consumption disabled |
| Expenses / customers / vendors | Implemented core forms | Recurring rules, documents, add-to-stock path, vendor bill links |
| Customer outstanding payments | Implemented | Financial reconciliation and concurrent double-payment prevention |
| Vendor settlement | Blocked | Source customer pay action posts an inflow; a correct deployed vendor settlement is needed |
| Staff / manager grants / attendance / advances | Implemented | Suspended/paid record rules and tenant enforcement |
| Payroll | History only | Server recomputation, idempotency, rounding and payment execution |
| Markets / services / labour / rentals | Implemented API views | Location units and shared-data edit authorization |
| Guidelines / blueprints | Implemented core | Apply blueprint and generated task scheduling parity |
| Documents | Implemented PDF/JPEG/PNG upload | File type/security limits and download permissions |
| Notifications | Inbox and read state | Firebase device registration, delivery and Android permission flow |
| Maps | Existing coordinates open external map | Embedded farm/crop boundary drawing and Maps project key |
| Payments | Package comparison only | Razorpay test-mode checkout and verified signed receipt flow |
| Offline / reconnect | Encrypted last-response fallback | No offline queued writes; relogin/access recovery with poor connectivity |
| Release | Debug build pipeline | Device QA, accessibility, localization, signing and Play Console rollout |

## Server findings from source review (not new runtime tests)

1. OTP sender includes a debug print of the phone number and OTP. Remove it before production.
2. Consumption creates an activity before decrementing stock; exceptions are swallowed and the
   decrement clamps to zero. A mobile quantity precheck cannot prevent simultaneous requests.
   Enforce tenant references and transactional stock validation on the server.
3. Customer outstanding `pay` uses client amounts, saves the balance and creates a ledger row
   without an atomic/idempotent transaction. It must reject overpayment and serialize payments.
   It does not implement a separate vendor outflow flow; vendor settlement is disabled here.
4. Payroll execution accepts gross/net/present totals supplied by the client, creates a payroll,
   then marks attendance/advances paid. Recompute and validate transactionally on the server.
5. Several API write paths expose only `IsAuthenticated`; workspace and module access must be
   enforced server-side for every read and mutation, regardless of client navigation.
6. The dashboard source constructs sample weather values. Do not present them as live weather.

These findings were derived from `apps/farmer/api_views.py` at the pinned base commit.
The existing deployed server and farm data were not changed by this branch.

## Verification recorded on 21 November 2026

App code commit `5c28b7dfe8f257b052984cbd61732da704da9ce5` passed debug APK/instrumentation compilation, Android lint, 14 unit/HTTP tests and 2 Android 10 emulator smoke tests in [run 44897833701](https://github.com/Nalinakumar-Agriot/Agriot/actions/runs/44897833701). This does not verify live farmer data, OTP delivery, financial reconciliation or the remaining release blockers above.
