# AgriOT Android replacement — review build

Native Kotlin / Jetpack Compose Android application using the existing Django farmer API.
This is an initial review build, not a production release or a claim of complete defect-free coverage.

## Build

Use JDK 17, Android SDK 35, Build Tools 35.0.0 and Gradle 8.13.
Open this directory in Android Studio or install Gradle 8.13 and run:

```sh
gradle :app:testDebugUnitTest :app:lintDebug :app:assembleDebug
gradle :app:connectedDebugAndroidTest
```

The GitHub Actions **Android review build** creates the debug APK and reports.
Download the **AgriOT-Android-review** artifact from a successful run and extract
`app-debug.apk`. It installs alongside the vendor app under a separate application ID.
The signing key is a debug key; this APK cannot replace an existing signed production installation.
A Gradle wrapper can be generated with `gradle wrapper --gradle-version 8.13`.
No production credentials, OTPs, payment keys or Firebase configuration are included.

## Server and account

The API is configured in `app/build.gradle.kts`:
`https://agriotdev.elysiumproduct.com/api/farmer/`.
The repository's Python URLs and serializers at base commit
`6e9b4c414f485ebc927826ce9cb4e3987436abc9` are the contract.
The checked-in `mobile_api_swagger.json` is older than that code and omits newer routes.
Deployment parity between this commit and the live server has not been verified.

Login and registration use form-encoded mobile/OTP requests. JWTs go only to the configured
HTTPS farmer API. Tokens and cached responses are encrypted using Android Keystore.
No authorization data is logged, backed up, committed or added to external links.
A 401 clears local access. No automatic mutation retries or queued offline writes.
Saved responses are labelled and cannot be edited offline.

## Included client workflows

- Farmer registration and OTP login; farmer/manager profile switching.
- Subscription and manager permission checks before exposing subscribed modules.
- Owner dashboard with real API values. Hardcoded server weather is deliberately not displayed.
- Farms, crops, task dates, managers, input consumption, farm activities and inventory.
- Customers, vendors, expenses, ledger, harvest, crop sales, customer invoice payments.
- Staff, manager module grants, attendance, advances and payroll history.
- Market prices/history, depots, services, labour, rentals, blueprints and guidelines.
- Document selection/upload through Android's file picker and an in-app notification inbox.
- Existing coordinates can open a map application; phone actions open the dialer.
- Forms use named relationship choices, positive/decimal/date validation and confirmation.
- PATCH sends changed fields, preserving fields not exposed in this client.
- List pagination is explicit. Search operates on loaded records.

These are implemented against source contracts. Live OTP, data mutation and cross-module
reconciliation must still be verified on a device with an authorized test account.

## Release blockers / remaining scope

See [COVERAGE.md](COVERAGE.md) and [QA_TRACKER.csv](QA_TRACKER.csv).
Paid subscriptions/checkout, Firebase push, an embedded boundary editor, richer farm
water/electricity/document rows, multi-grade harvest entry,
payroll execution, vendor settlements, full recurring expense editing and production signing
are not finished. The client must not present those functions as verified.
Existing backend accounting/concurrency/permission gaps need server-side remediation and tests.
Do not merge or deploy until the review and device acceptance checks pass.

## Test coverage

Business regression tests cover negative acreage, invalid phone numbers, preservation of saved
location values during note edits, explicit clearing, unlimited limits, exact decimal totals,
coordinate/date validation and subscription/manager grant intersection.
HTTP tests cover form OTP requests, bearer headers, session expiry, unsafe pagination URLs,
uncertain writes without automatic replay, permission failures and pagination.
Instrumented smoke tests launch the native login/registration screens without sending an SMS.
