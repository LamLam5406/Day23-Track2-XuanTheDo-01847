"""BƯỚC 3c — SINH VIÊN VIẾT. Tự động hoá runbook §4 "Runbook: Region Chính Down".

7 bước trên slide, mỗi bước 1 dòng log có ts. Log này CHÍNH LÀ timeline của postmortem.
  1 xac_nhan_outage          — probe cả 2 region, đừng tin 1 lần fail (dùng nhiều lần
                              hoặc gọi health_checker.probe nếu đã viết xong 3a)
  2 thong_bao_incident       — ts của dòng này là mốc "operator biết tin", LUÔN LUÔN
                              SAU t_outage trong chaos-events (không thể trùng — operator
                              không thể biết ngay giây outage xảy ra). Ghi cả 2 ts vào
                              log để postmortem tính được "độ trễ thông báo".
  3 scale_gpu_pool           — gọi HÀM `failover.failover(...)` MỘT LẦN DUY NHẤT. Hàm
                              đó tự làm đủ 5 bước con (verify/restore/scale/wait/cutover)
                              và tự ghi log riêng vào reports/failover-events.jsonl.
  4 verify_state_replica     — KHÔNG gọi lại failover — chỉ ĐỌC kết quả (vector count +
                              weights ở region phụ) từ dict mà bước 3 trả về, để log vào
                              runbook-run.jsonl cho postmortem đọc 1 chỗ duy nhất.
  5 dns_cutover              — cũng chỉ đọc lại: kết quả cutover có ok hay không.
  6 verify_golden_signals    — 10 request thật vào region phụ: p95 latency + error rate
  7 post_incident            — elapsed_s + lệnh đo RTO

BÁN TỰ ĐỘNG, KHÔNG FULL-AUTO (§4: "failover đầu tiên nên là bán tự động — alert +
1-click confirm — tránh flapping gây failover 2 chiều liên tục"). Mặc định phải hỏi
người vận hành confirm; --auto chỉ dùng trong CI/khi chấm điểm.

Chạy:  python dr/runbook.py --primary a --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from dr import failover as fo  # noqa: E402
from dr.health_checker import probe  # noqa: E402

LOG = pathlib.Path("reports/runbook-run.jsonl")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def step(n, name, **kw):
    """Append one runbook event."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
           "step": n, "name": name, **kw}
    with LOG.open("a", encoding="utf-8") as log:
        log.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("RUNBOOK", json.dumps(rec, ensure_ascii=False))
    return rec


def confirm(auto: bool, msg: str) -> bool:
    """Require an operator confirmation unless explicitly running in CI mode."""
    return True if auto else input(f"{msg} [y/N] ").strip().lower() in {"y", "yes"}


def run(primary: str, target: str, backend: str, auto: bool) -> dict:
    """Execute the seven-step, semi-automated incident runbook."""
    started = time.time()
    checks = []
    for attempt in range(3):
        primary_ready, primary_reason = probe(primary, 2.0)
        target_ready, target_reason = probe(target, 2.0)
        checks.append({"attempt": attempt + 1, "primary_ready": primary_ready,
                       "primary_reason": primary_reason, "target_ready": target_ready,
                       "target_reason": target_reason})
        if primary_ready:
            break
        if attempt < 2:
            time.sleep(1.0)
    outage = len(checks) == 3 and all(not item["primary_ready"] for item in checks)
    step(1, "xac_nhan_outage", primary=primary, target=target, confirmed=outage, checks=checks)
    if not outage:
        return {"ok": False, "error": "primary outage not confirmed", "checks": checks}
    if not confirm(auto, f"Region {primary} is unavailable. Fail over to region {target}?"):
        step(2, "thong_bao_incident", confirmed=False, action="operator_aborted")
        return {"ok": False, "error": "operator declined failover"}
    notified = step(2, "thong_bao_incident", confirmed=True,
                    outage_observed_at=started, notification_delay_s=round(time.time() - started, 2))
    result = fo.failover(target, backend, wait=60.0)
    step(3, "scale_gpu_pool", failover_ok=result.get("ok"), waited_s=result.get("waited_s"),
         error=result.get("error"))
    step(4, "verify_state_replica", state=result.get("state"), restore=result.get("restore"),
         rpo=result.get("rpo"))
    step(5, "dns_cutover", ok=result.get("ok"), active_region=result.get("active_region"))
    if not result.get("ok"):
        return result
    latencies, errors, served_by = [], 0, []
    for i in range(10):
        t0 = time.monotonic()
        try:
            response = httpx.get(f"{URL[target]}/v1/infer", params={"q": f"golden-{i}"}, timeout=3.0)
            body = response.json()
            latencies.append((time.monotonic() - t0) * 1000)
            served_by.append(body.get("region"))
            errors += int(response.status_code != 200)
        except httpx.HTTPError:
            latencies.append((time.monotonic() - t0) * 1000)
            errors += 1
    ordered = sorted(latencies)
    p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))] if ordered else None
    step(6, "verify_golden_signals", requests=10, error_rate=errors / 10,
         p95_latency_ms=None if p95 is None else round(p95, 1), served_by=served_by)
    elapsed = round(time.time() - started, 2)
    command = "python tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300"
    step(7, "post_incident", elapsed_s=elapsed, measure_command=command,
         incident_notified_at=notified["ts"])
    return {**result, "elapsed_s": elapsed, "golden_error_rate": errors / 10,
            "golden_p95_latency_ms": p95}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--primary", default="a")
    p.add_argument("--target", default="b")
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--auto", action="store_true")
    a = p.parse_args()
    print(json.dumps(run(a.primary, a.target, a.backend, a.auto), indent=2))
