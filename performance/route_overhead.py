"""Micro-benchmark for ClearView router overhead.

Measures deterministic rule-path routing only, so it does not call providers or
the classifier. Intended as a quick regression check for the <100ms p95 target.

Usage:
    python performance/route_overhead.py --iterations 10000
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import load_policy  # noqa: E402
from app.router import route  # noqa: E402


PROMPTS = [
    "hi",
    "Traceback (most recent call last):\n  File \"app.py\", line 2",
    "derive x^2 = 4",
    "fix app/router.py",
    "read https://example.com/logs",
    "def f(x):\n  return x + 1",
    "please refactor this module " + ("x " * 800),
    "x" * 16001,
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=10000)
    ap.add_argument("--policy", default=str(ROOT / "policy.yaml"))
    args = ap.parse_args()

    pol = load_policy(args.policy)
    samples_ms: list[float] = []
    n = max(1, int(args.iterations))
    for i in range(n):
        prompt = PROMPTS[i % len(PROMPTS)]
        t0 = time.perf_counter()
        route(prompt, pol)
        samples_ms.append((time.perf_counter() - t0) * 1000.0)

    samples_ms.sort()
    p50 = statistics.median(samples_ms)
    p95 = samples_ms[int((len(samples_ms) - 1) * 0.95)]
    p99 = samples_ms[int((len(samples_ms) - 1) * 0.99)]
    print(f"iterations={n}")
    print(f"p50_ms={p50:.4f}")
    print(f"p95_ms={p95:.4f}")
    print(f"p99_ms={p99:.4f}")
    print(f"max_ms={max(samples_ms):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
