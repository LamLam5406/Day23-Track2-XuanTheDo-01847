# One-page runbook - primary region down

Scope: local bare mode, primary `a`, target `b`, filesystem snapshot backend. RTO and RPO targets are 300 seconds.

| # | Step | Copy-paste command | Completion signal | Owner |
|---|---|---|---|---|
| 1 | Confirm outage | `python chaos/kill_region.py status` | `a.ready=false` for three probes and `b.alive=true` | on-call SRE |
| 2 | Declare incident/start RTO clock | `python dr/runbook.py --primary a --target b --backend fs` | operator answers `y` and `thong_bao_incident` is logged | incident commander |
| 3 | Restore target state | `python state/snapshot.py get --region b --backend fs` | manifest has model version and B vector count is positive | storage on-call |
| 4 | Scale warm to full | `python dr/failover.py --target b --backend fs` | B `/readyz` returns HTTP 200; abort on timeout | serving on-call |
| 5 | Verify DNS/LB cutover | `python -c "from pathlib import Path; print(Path('edge/active_region').read_text().strip())"` | output is `b` and failover log contains `5_dns_cutover` | incident commander |
| 6 | Verify golden signals | `python -c "import httpx; print([httpx.get('http://127.0.0.1:8002/v1/infer').status_code for _ in range(10)])"` | 10/10 HTTP 200, zero errors, p95 below 1000ms, served by B | serving on-call |
| 7 | Measure and review | `python tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300` | `valid:true`, no warnings, and `rto_verdict:PASS` | incident commander |

Normal operation uses step 2 as the semi-automated entrypoint; it calls the failover sequence once. Steps 3-6 are individual recovery/verification commands for manual diagnosis.

## Rollback

Only the incident commander may return traffic to A. A must have current restored data, five continuous minutes of readiness, replication lag below 300s, passing golden tests, and an isolated outage cause. If B exceeds 1% errors or 1000ms p95 before A meets every condition, escalate and keep traffic on B. Never enable automatic failback, which could flap both regions.
