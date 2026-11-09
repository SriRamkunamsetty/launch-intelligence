# Portfolio notes

A launch-analytics tool whose written output can be audited: every figure in the narrative traces to a computed fact.

## Bullets you can defend today (each is backed by the test suite or a screenshot in this repo)
- Built an entertainment launch-analytics engine (velocity, share of voice, genre benchmarks, robust anomaly detection linked to campaign events) over a normalised SQL schema with SQLite/Postgres support.
- Implemented a YouTube Data API collector with quota accounting, retry/backoff and a scheduled worker, plus validated CSV ingestion that rejects bad rows with reasons.
- Shipped a read-only dashboard and a one-click executive deck that refuses to build when its narrative fails claim verification; 19 automated tests including real-browser checks.

## Add these only after you have measured them yourself (do not publish a number you have not measured)
- Collected [N] days of real snapshots for [M] titles.
- Time saved producing the weekly report: [measure it].

## Interview questions this project prepares you for
**Why interpolate instead of using the nearest snapshot?**  
Nearest-snapshot biases a 72h figure by up to a whole sampling interval; interpolation anchored at (publish, 0) is unbiased for smooth growth, and an unobserved window returns nothing rather than a guess.

**How do you avoid claiming a TV spot caused a spike?**  
The tool reports alignment within a window and says so in the text; attributing lift needs a controlled repeat or a holdout.

**Why merge consecutive anomalous days?**  
A spike plus its rebound is one event. Reporting the first flagged day understated the peak (262K vs 2.9M views in my first version).
