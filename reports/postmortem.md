# Postmortem - DR Drill Lab 23

## 1. Timeline

| ISO time | Event | Evidence |
|---|---|---|
| 2026-08-25T05:08:17Z | Region A outage starts | `chaos/chaos-events.jsonl:2` |
| 2026-08-25T05:08:19Z | first failed request after outage | `reports/drill-2-withdr.jsonl:3` |
| 2026-08-25T05:08:33Z | checker marks A UNHEALTHY after three failures | `reports/health-events.jsonl:2` |
| 2026-08-25T05:08:40Z | operator/runbook confirms incident | `reports/runbook-run.jsonl:2` |
| 2026-08-25T05:08:47Z | target ready and DNS cut over to B | `reports/failover-events.jsonl:5` |
| 2026-08-25T05:08:49Z | first successful request from B | `reports/drill-2-withdr.jsonl:15` |

## 2. RTO/RPO gap analysis

- RTO target: 300s. Measured RTO: 32.3s. Headroom/gap: 267.7s. Health detection at 15.9s was the largest component.
- RPO target: 300s. Measured RPO: 0.0s and 0 docs lost. Headroom/gap: 300.0s (`reports/failover-events.jsonl:2`).
- Process gap: sequential outage confirmation consumed about 8s before restore. It did not breach the SLO but could grow with a longer endpoint timeout.

## 3. Root cause - 5 whys

1. Users saw errors because edge still routed to the stopped Region A.
2. Edge had not changed route because cutover waits for Region B readiness.
3. B was not ready because it began warm and lacked weights and vectors.
4. State is restored during the incident instead of keeping a full-time duplicate to save resources.
5. Thresholds and operator confirmation exist to prevent transient failures and flapping from causing cutover.

For a real outage, the greatest risk is a missing/stale snapshot or incompatible model version. The runbook aborts before DNS cutover when restore or readiness fails, preventing a double outage.

## 4. Action items

| # | Action item | Owner | Deadline | Expected improvement |
|---|---|---|---|---|
| 1 | Parallelize confirmation probes and pass the alert into the runbook | SRE | 2026-09-01 | reduce RTO by 6-8s |
| 2 | Alert on replication lag and validate model version before drills | Storage owner | 2026-09-03 | keep RPO below 300s and prevent restore failures |
| 3 | Pre-warm B during high-risk windows | Serving owner | 2026-09-05 | reduce RTO by about 6.5s |

## 5. Required questions

1. `interval * threshold = 5s * 3 = 15s`, about 46.4% of the 32.3s RTO. Observed detection was 15.9s due to polling phase and timeout.
2. Reducing interval from 5s to 1s makes the floor 3s and can save up to 12s. Probe load becomes five times higher and transient failures pose more flapping risk, so threshold, cooldown, and circuit breaker remain necessary.
3. In a six-hour permanent primary loss, `docs_lost` counts customer writes present in A but absent from the restored B snapshot. This is real data loss requiring reconciliation or replay from the ingest source.
