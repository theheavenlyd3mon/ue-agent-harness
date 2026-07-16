"""Generate a simple report from metrics.jsonl."""

import json
from pathlib import Path


def summarize(metrics_path: Path = Path("sessions/metrics.jsonl")) -> dict:
    total = 0
    success = 0
    build_attempts = 0
    for line in Path(metrics_path).read_text().splitlines():
        if not line.strip():
            continue
        m = json.loads(line)
        total += 1
        if m.get("build_success"):
            success += 1
        build_attempts += m.get("build_attempts", 0)
    return {
        "total_sessions": total,
        "build_success_rate": success / total if total else 0,
        "average_build_attempts": build_attempts / total if total else 0,
    }
