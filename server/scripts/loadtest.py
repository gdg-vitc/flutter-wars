"""Concurrent load smoke test for the Worker endpoints.

Usage:
    uv run python scripts/loadtest.py <url> [requests] [concurrency]
    uv run python scripts/loadtest.py http://127.0.0.1:8787/ready 100 25

Reports success rate, throughput, wall time and p50/p95/p99 latency plus a
sample failure. Exit code is non-zero if any request failed.
"""

from __future__ import annotations

import asyncio
import sys
import time

import httpx


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    rank = int(round((percentile / 100.0) * (len(ordered) - 1)))
    return ordered[max(0, min(rank, len(ordered) - 1))]


async def _one(client: httpx.AsyncClient, url: str, results: list, index: int) -> None:
    start = time.perf_counter()
    try:
        response = await client.get(url)
        ok = response.status_code < 500
        detail = response.text[:200]
    except Exception as exc:  # noqa: BLE001
        ok = False
        detail = f"{type(exc).__name__}: {exc}"[:200]
    results[index] = (ok, (time.perf_counter() - start) * 1000.0, detail)


async def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787/health"
    total = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    concurrency = int(sys.argv[3]) if len(sys.argv) > 3 else 5

    results: list = [None] * total
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(timeout=60.0) as client:

        async def guarded(i: int) -> None:
            async with semaphore:
                await _one(client, url, results, i)

        started = time.perf_counter()
        await asyncio.gather(*(guarded(i) for i in range(total)))
        wall = time.perf_counter() - started

    latencies = [entry[1] for entry in results]
    failures = [entry for entry in results if not entry[0]]
    ok_count = total - len(failures)

    print(f"url={url} requests={total} concurrency={concurrency}")
    print(
        f"success={ok_count}/{total} failures={len(failures)} wall={wall:.3f}s throughput={total / wall:.1f} req/s"
    )
    print(
        "latency_ms "
        f"min={min(latencies):.1f} "
        f"p50={_percentile(latencies, 50):.1f} "
        f"p95={_percentile(latencies, 95):.1f} "
        f"p99={_percentile(latencies, 99):.1f} "
        f"max={max(latencies):.1f}"
    )
    if failures:
        print("failure sample:", failures[0][2])
    return 0 if ok_count == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
