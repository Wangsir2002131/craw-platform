"""Basic end-to-end stress test for Phase E APIs."""

from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.error
import urllib.request
from statistics import mean


def request_json(url: str) -> tuple[int, float]:
    started = time.perf_counter()
    with urllib.request.urlopen(url, timeout=10) as response:
        response.read()
        elapsed = time.perf_counter() - started
        return response.status, elapsed


def worker(base_url: str, path: str, iterations: int, results: list[dict[str, float]]) -> None:
    for _ in range(iterations):
        url = f"{base_url.rstrip('/')}{path}"
        try:
            status_code, elapsed = request_json(url)
            results.append({"ok": 1.0 if status_code < 500 else 0.0, "elapsed": elapsed})
        except urllib.error.URLError:
            results.append({"ok": 0.0, "elapsed": 0.0})


def run_stress(base_url: str, concurrency: int, iterations: int, path: str) -> dict[str, float]:
    threads = []
    results: list[dict[str, float]] = []
    started = time.perf_counter()

    for _ in range(concurrency):
        thread = threading.Thread(target=worker, args=(base_url, path, iterations, results))
        thread.start()
        threads.append(thread)

    for thread in threads:
        thread.join()

    duration = time.perf_counter() - started
    total_requests = len(results)
    success_count = sum(int(item["ok"]) for item in results)
    latencies = [item["elapsed"] for item in results if item["elapsed"] > 0]

    return {
        "total_requests": total_requests,
        "success_count": success_count,
        "success_rate": round(success_count / total_requests, 4) if total_requests else 0.0,
        "avg_latency_seconds": round(mean(latencies), 4) if latencies else 0.0,
        "throughput_rps": round(total_requests / duration, 2) if duration else 0.0,
        "duration_seconds": round(duration, 4),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase E API stress test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="service base URL")
    parser.add_argument("--path", default="/health", help="target API path")
    parser.add_argument("--concurrency", type=int, default=10, help="number of worker threads")
    parser.add_argument("--iterations", type=int, default=20, help="requests per worker")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_stress(args.base_url, args.concurrency, args.iterations, args.path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
