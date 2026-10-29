# Monthly reliability report

Left behind by the previous team. Marta uses it every Monday to decide which pump to inspect.

```bash
python -m legacy.report --month 2026-08 --out report.csv
```

| Column | What it is |
|---|---|
| `availability_pct` | % of the month the pump was not stopped by a failure |
| `n_failures` | corrective work orders opened in the month |
| `operating_hours` | running hours |
| `mtbf_hours` | mean time between failures (running hours / failures) |
| `vib_p95` | 95th percentile of vibration |

Tests: `uv run pytest legacy/report/tests`.
