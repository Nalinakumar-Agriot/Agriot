# AgriOT Android review — 21 November 2026

This is a native Android review build configured for the existing development farmer API.
The full production replacement is not finished. Use the coverage matrix to see remaining scope.

## Download and install
- [APK and test reports](https://github.com/Nalinakumar-Agriot/Agriot/actions/runs/44897833701/artifacts/14704542752)
- Extract the ZIP and open `outputs/apk/debug/app-debug.apk` on an Android 8.0 or newer device.
- The installed name is **AgriOT Review**. It uses a separate application ID from the vendor app.
- Sign in with an existing registered farmer/manager phone number and the SMS verification code.
- The build is configured for `agriotdev.elysiumproduct.com`. Its deployed API version must be checked during live acceptance.

## Evidence
- Tested code commit: `5c28b7dfe8f257b052984cbd61732da704da9ce5`.
- [Build evidence](https://github.com/Nalinakumar-Agriot/Agriot/actions/runs/44897833701): debug and instrumentation APK compile, lint, 14 unit/HTTP tests passed.
- Final emulator evidence: 2 Android 10 emulator smoke tests passed, with 0 failures in the same CI run..
- APK artifact expires on 5 December 2026; the source and workflow can generate another build.
- [Source review](https://github.com/Nalinakumar-Agriot/Agriot/pull/1).
- [Coverage](android/COVERAGE.md) and [QA tracker](android/QA_TRACKER.csv).

## Live acceptance sequence
Use an authorized development account and clearly labelled QA records. These are proposed test inputs, not executed or saved records.

| Step | Enter / perform | Verify |
|---|---|---|
| Access | OTP login; try wrong/expired code separately | Correct profile and enabled modules; no access to another farmer |
| Inventory | QA Review Fertilizer, 20 Kg, INR 120 per Kg | Quantity 20; stock valuation INR 2,400 |
| Farm | QA Review North, 2 acres; select state/district/taluk | Save, reopen, change notes; location must remain intact |
| Crop | Tomato, 1 acre, multiple harvest, 3 flushes, 500 Kg expected yield | Correct farm, managers, unit and remaining land |
| Workforce | Add authorized test employee and manager numbers | Names, permissions, attendance and rates are retained |
| Task | Irrigation task linked to farm/crop, chosen future date and employee | Correct date, assignee and completion status after reopening |
| Consumption | Use 2 Kg of QA Review Fertilizer | Stock 18 Kg; calculated value INR 2,160; one activity |
| Expense | Vendor bill INR 300, balance INR 200 | Correct vendor, farm and due amount; no duplicate cost |
| Harvest | Advance crop to Yield; record 10 Kg at INR 100/Kg, Grade 1 | Harvest stock, unit, grade and stage agree |
| Sale | Sell 10 Kg, INR 100/Kg, GST 5%, discount INR 100, commission INR 50, paid INR 400 | Net INR 900, due INR 500; correct customer |
| Settlement | Record remaining customer payment INR 500 | Due zero; paid INR 900; sales and ledger reconcile |
| Connectivity | Lose connection during save, restore and refresh | Uncertain result is explicit; no blind replay/duplicate records |
| Files/inbox | Upload a small test PDF; open document; mark a notification read | Correct file, account scope and notification state |
| Device | Rotate, increase font size, use TalkBack and back navigation | Form values and controls remain usable |

Do not use this build to validate real payroll disbursement, vendor settlement, paid subscription
checkout or push delivery. Those workflows remain blocked or incomplete as recorded in the tracker.
Firebase and Maps will use the existing AgriOT projects when their configuration is available.
