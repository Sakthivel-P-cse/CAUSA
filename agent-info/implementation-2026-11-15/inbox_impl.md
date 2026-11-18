# Inbox relative window implementation

## Scope

Scope is limited to `IncidentInbox.jsx`, its focused regression test, `Statistics.jsx`, and `Overview.jsx`. The inbox's `now` state is initialized once and reused for relative query bounds, so a long-open inbox keeps sending the mount-time window. `DashboardProvider` supplies relative `minutes` or absolute `from` plus `to`; `IncidentTable` also uses `now` to derive age labels. Both KPI pages currently read `auto_remediation_rate` and show abbreviated automatic-remediation labels; the backend's field is `remediation_success_rate` and counts remediation outcomes generally.

## Plan

Add a component regression using fake timers. It should observe relative query bounds advancing at the next rounded minute boundary, while an explicit `from`/`to` window remains fixed and relative timer cleanup happens on unmount. Then add the smallest conditional timer in the page. Update both KPI data field references and labels only.

## Changes

- Added a conditional timeout aligned to minute boundaries for relative inbox windows; it updates `now` and clears on unmount. Complete explicit windows do not schedule it.
- Added fake-timer tests for relative-bound advancement, explicit-window stability, and cleanup.
- Changed Statistics and Overview to read `remediation_success_rate` and label it “Remediation success.”

## Checks

- Red: focused regression failed because the request bounds stayed at 11:50–12:05 after advancing to 12:06.
- Green: focused file, 2 tests passed.
- Full frontend Vitest: 7 files, 10 tests passed.
- Frontend lint passed.
- Frontend production build passed.

## Limits

No live browser/API run. No dependencies added. No commit made.
