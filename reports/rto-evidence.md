# RTO/RPO Evidence - Lab 23

All values below come from the 2026-08-25 drill timestamps.

## 1. Drill 1 - no DR

| Metric | Value | Measurement | Evidence |
|---|---:|---|---|
| t_outage | 2026-08-25T05:07:17Z | chaos kill event | `chaos/chaos-events.jsonl:1` |
| First failed request after outage | +2.3s | first later `ok:false` | `reports/drill-1-nodr.jsonl:8` |
| Successful request after failure | None | no later `ok:true` | `reports/drill-1-nodr.jsonl:10` |
| RTO | NO_RECOVERY | no success exists after the first failure | `reports/drill-1-nodr.jsonl:10` |

## 2. Drill 2 - with DR

| Milestone | Seconds from outage | Measurement | Evidence |
|---|---:|---|---|
| Outage | 0.0s | Region A kill | `chaos/chaos-events.jsonl:2` |
| First user-visible error | 2.1s | first failed request after t_outage | `reports/drill-2-withdr.jsonl:3` |
| Health checker detection | 15.9s | three consecutive failures at 5s interval | `reports/health-events.jsonl:2` |
| Snapshot restored | 23.9s | `2_restore_snapshot` | `reports/failover-events.jsonl:2` |
| Region B ready | 30.4s | `4_wait_ready`, waited 6.48s | `reports/failover-events.jsonl:4` |
| DNS cutover | 30.4s | `5_dns_cutover` | `reports/failover-events.jsonl:5` |
| First success from B | 32.3s | first later `ok:true`, `served_by:b` | `reports/drill-2-withdr.jsonl:15` |

| Metric | Measured | Target | Verdict |
|---|---:|---:|---|
| RTO - Inference API | 32.3s | 300s | PASS |
| RPO - Vector DB | 0.0s / 0 docs | 300s | PASS |

The primary and restored `latest_doc_ts` match, so this drill lost zero documents (`reports/failover-events.jsonl:2`).

## 3. RTO breakdown

| Component | Seconds | Source | Reduction option |
|---|---:|---|---|
| Health-check detection | 15.9s | 5.0s interval and threshold 3 in `reports/health-events.jsonl:2` | carefully reduce interval while retaining anti-flap threshold |
| Confirmation, verification, restore | 8.0s | detection through `2_restore_snapshot` | parallelize probes and consume the existing alert |
| GPU pool warm-up | 6.5s | `waited_s:6.48` in `reports/failover-events.jsonl:4` | keep a warm or pre-warmed pool |
| DNS cache and request cadence | 1.9s | recovery request minus cutover | lower TTL and use jittered retries |
| **Measured total** | **32.3s** | kill through first success from B | - |

The configured detection floor is 15.0s. The observed 15.9s also includes polling phase and timeout effects.
