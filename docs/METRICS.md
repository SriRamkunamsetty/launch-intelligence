# Metric dictionary: Launch Intelligence

Every number the narrator may cite is a `Fact` with an id and a provenance string. Keep this file in sync with `metrics.py`.

| Fact id | Definition | Notes |
|---|---|---|
| `n_posts` | Posts tracked for the title | Compare titles with similar post counts |
| `velocity_{24h,72h,7d}` | Sum over posts of cumulative views at +N hours after each post's publish time | Linear interpolation between snapshots, anchored at (publish, 0). **None if the window has not been observed**; never extrapolated |
| `eng_rate` | (likes + comments + shares) / views, from each post's latest snapshot, view-weighted across posts | Denominator is views, not followers or reach. Platforms differ on what counts as a view |
| `sov_7d` | Title's 7-day views / sum of 7-day views over the competitive set | View-based share of voice, not mention-based |
| `benchmark_72h` | Percentile rank of 72h velocity among same-genre titles (ties count half) | Needs 3+ titles; `benchmark_n` is reported because small samples are unstable |
| `anomaly_k_day`, `anomaly_k_views` | Day-over-day log growth of daily views; robust z (MAD, floored at 0.05) >= 3.5. Consecutive flagged days merge into one episode; the peak day is reported | Events within +/-48h are attached. Timing alignment is **not** causal proof |

## Verification rule
`narrator.verify()` extracts every numeric claim from a text and checks it against the Fact set (1% tolerance). Window labels (`72 hours`, `7-day`) and bare years are not treated as claims.
