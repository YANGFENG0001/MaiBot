"""Verify the literal Phase 6 manifest against real marked pytest collection."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pytests"))

from phase6_matrix.manifest import PHASE6_EXACT_NODES  # noqa: E402


def main() -> int:
    command = [
        sys.executable, "-m", "pytest", "pytests/phase6_matrix",
        "-m", "phase6_matrix", "--collect-only", "-q", "-p", "no:cacheprovider",
    ]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    combined = "\n".join(part for part in (result.stdout, result.stderr) if part)
    collected = [
        line.strip().replace("\\", "/")
        for line in result.stdout.splitlines()
        if line.strip().startswith("pytests") and "::" in line
    ]
    expected = list(PHASE6_EXACT_NODES)
    counts = Counter(collected)
    duplicates = sorted(node for node, count in counts.items() if count != 1)
    missing = sorted(set(expected) - set(collected))
    extra = sorted(set(collected) - set(expected))
    ok = result.returncode == 0 and not duplicates and not missing and not extra and collected == expected
    if ok:
        print("127/127 exact collected nodes")
        return 0
    print("Phase 6 exact-node verification failed", file=sys.stderr)
    print(f"pytest_exit={result.returncode} collected={len(collected)} expected={len(expected)}", file=sys.stderr)
    if duplicates:
        print("duplicates: " + ", ".join(duplicates), file=sys.stderr)
    if missing:
        print("missing: " + ", ".join(missing), file=sys.stderr)
    if extra:
        print("extra: " + ", ".join(extra), file=sys.stderr)
    if not missing and not extra and collected != expected:
        print("collection order differs from literal manifest", file=sys.stderr)
    if combined:
        print(combined, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
