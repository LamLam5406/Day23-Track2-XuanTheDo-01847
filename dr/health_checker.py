"""BƯỚC 3a — SINH VIÊN VIẾT. Health checker cho 2 region.

Yêu cầu (đọc §4 "Kiến Trúc Health-Check-Based Failover" + §2 "DNS Failover"):
  1. Poll /readyz của CẢ HAI region mỗi `interval` giây (mặc định 5s).
     Dùng /readyz, KHÔNG dùng /healthz. /healthz chỉ nói "process còn sống" —
     region có process sống nhưng vector DB rỗng thì vẫn không serve được.
  2. Chỉ đổi trạng thái sau `threshold` lần fail LIÊN TIẾP (mặc định 3).
     Một lần fail không phải outage. Đây là chống flapping (§4 Anti-Patterns).
  3. Ghi 1 dòng JSONL MỖI LẦN ĐỔI TRẠNG THÁI (không ghi mỗi lần poll — log sẽ ngập).
     Dòng bắt buộc có: ts, region, to (HEALTHY|UNHEALTHY), reason,
     interval_s, threshold. Thiếu interval_s/threshold thì tools/measure_rto.py
     không tính được detect floor -> mất điểm.

Chạy:  python dr/health_checker.py --interval 5 --threshold 3 --duration 300 \
              --out reports/health-events.jsonl

CÂU HỎI PHẢI TRẢ LỜI TRƯỚC KHI VIẾT (ghi câu trả lời vào reports/postmortem.md):
  interval=5s, threshold=3 -> sớm nhất bạn có thể phát hiện outage là bao nhiêu giây?
  Con số đó nằm TRONG RTO của bạn. Muốn RTO 5 phút thì được phép chọn interval bao nhiêu?
"""
import argparse
import json
import pathlib
import time

import httpx

URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def probe(region: str, timeout: float) -> tuple[bool, str]:
    """Return readiness and a diagnostic reason from the readiness endpoint."""
    try:
        response = httpx.get(f"{URL[region]}/readyz", timeout=timeout)
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code == 200 and body.get("ready", True):
            return True, "readyz_200"
        reasons = body.get("reasons") or [f"readyz_status_{response.status_code}"]
        return False, ",".join(str(reason) for reason in reasons)
    except httpx.TimeoutException:
        return False, "readyz_timeout"
    except httpx.HTTPError as exc:
        return False, f"{type(exc).__name__}: {exc}"


def run(interval: float, timeout: float, threshold: int, duration: float, out: pathlib.Path):
    """Poll both regions and log state transitions after consecutive failures."""
    if interval <= 0 or timeout <= 0 or threshold < 1 or duration < 0:
        raise ValueError("invalid health-check timing configuration")
    out.parent.mkdir(parents=True, exist_ok=True)
    states = {region: "HEALTHY" for region in URL}
    failures = {region: 0 for region in URL}
    deadline = time.monotonic() + duration
    with out.open("a", encoding="utf-8") as log:
        while time.monotonic() < deadline:
            cycle_started = time.monotonic()
            for region in URL:
                ready, reason = probe(region, timeout)
                failures[region] = 0 if ready else failures[region] + 1
                next_state = states[region]
                if ready and states[region] == "UNHEALTHY":
                    next_state = "HEALTHY"
                elif not ready and failures[region] >= threshold:
                    next_state = "UNHEALTHY"
                if next_state != states[region]:
                    record = {
                        "ts": time.time(), "region": region, "event": "state_change",
                        "from": states[region], "to": next_state, "reason": reason,
                        "consecutive_fails": failures[region], "interval_s": interval,
                        "threshold": threshold,
                    }
                    log.write(json.dumps(record, ensure_ascii=False) + "\n")
                    log.flush()
                    states[region] = next_state
            remaining = deadline - time.monotonic()
            sleep_for = interval - (time.monotonic() - cycle_started)
            if remaining > 0 and sleep_for > 0:
                time.sleep(min(sleep_for, remaining))
    return states


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--timeout", type=float, default=2.0)
    p.add_argument("--threshold", type=int, default=3)
    p.add_argument("--duration", type=float, default=300)
    p.add_argument("--out", default="reports/health-events.jsonl")
    a = p.parse_args()
    run(a.interval, a.timeout, a.threshold, a.duration, pathlib.Path(a.out))
