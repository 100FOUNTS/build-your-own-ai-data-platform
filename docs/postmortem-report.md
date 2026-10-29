# Postmortem — the monthly reliability report gave false figures (INF-2026-10)

Blameless format: the goal is to understand why the system allowed the error, not who wrote it.

## Summary
The monthly report Maintenance uses to prioritize pumps had carried four computation errors for months. It overstated availability,
multiplied the MTBF by five, lost failures that happen close to midnight on the last day of the month and understated the p95 vibration.
The computation was fixed without changing the report format and five regression tests were added.

## Impact
- August 2026: P104 showed 99.34% availability when it was 92.9% (52.9 h stopped). Same for P102 and P111.
- The MTBF came out 5× too high on every pump with failures: Maintenance could have stretched inspection intervals believing the pumps failed less.
- P112's failure on 08-31 (23:32 UTC) didn't show up in August and its downtime (~54 h) wasn't deducted in any month.
- The vibration p95 included instants with the pump stopped (~0.02 mm/s), so it came out lower on the pumps with the most stoppages, which are exactly the ones that matter most.

## Timeline
1. 2025: the previous team writes the report with two tests (row count and availability range), which pass.
2. 2026-10: Marta notices that P104 “can't have been 99% available” and opens the ticket.
3. It is reproduced with `python -m legacy.report --month 2026-08` and compared by hand against the work orders and the history.

## Root cause
1. **Time zone**: the month was cut in `Europe/Madrid` (the historian server time), but the platform defines days and months in UTC.
   Everything between 22:00 and 24:00 UTC on the last day went to the next month.
2. **`.dt.seconds` instead of `.dt.total_seconds()`**: `.seconds` is only the seconds component (0–86,399) of a `Timedelta`; it ignores the days.
   A stoppage of 2 days and 5 hours counted as 5 hours.
3. **Stoppages that cross months**: the downtime was attributed entirely to the month in which the work order was opened, without clipping it to the month boundaries.
4. **Operating hours ×5**: it counted every row (one per sensor) of the running instants, not the instants.
5. **p95 without filtering for running**: the percentile was computed over every vibration reading.
6. Why nobody saw it: the existing tests only checked the shape (12 rows, 0–100%), never a known value. And 3,720 operating hours in a 744-hour month had no plausibility check at all.

## Fix
- UTC months with explicit `[start, end)` boundaries; downtime is clipped to those boundaries with `clip` and measured with `total_seconds()`.
- Operating hours = instants with `RUN_STATE = 1` × 10 min; the p95 uses only those instants.
- The report format (columns and order) didn't change: consumers don't have to do anything.

## Prevention
- Five regression tests in `legacy/report/tests/test_regressions.py`, each one with a real case that used to fail.
- A plausibility test: `operating_hours` ≤ hours in the month and `availability_pct` consistent with the downtime hours.
- Team rule: time cuts use the contract's time zone (UTC) written as a named constant, never the server's.
- Pending: check with Marta whether July (which starts on the 3rd, when the history begins) should be measured over the whole month or over the days with data.
