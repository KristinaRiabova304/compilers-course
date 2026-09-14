"""Runs every program in tests/ through compiler.py and checks its output.

valid_*.txt   must compile and, run through `lli`, print the matching
              valid_*.expected on stdout.
invalid_*.txt must fail to compile with the exact stderr line in the
              matching invalid_*.expected.
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS_DIR = pathlib.Path(__file__).resolve().parent


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def check_valid(src: pathlib.Path, expected: str) -> str | None:
    ll_path = src.with_suffix(".ll")
    compiled = run([sys.executable, str(ROOT / "compiler.py"), str(src), str(ll_path)])
    if compiled.returncode != 0:
        return f"compilation failed: {compiled.stderr.strip()}"
    ran = run(["lli", str(ll_path)])
    ll_path.unlink(missing_ok=True)
    if ran.returncode != 0:
        return f"lli exited {ran.returncode}: {ran.stderr.strip()}"
    if ran.stdout.strip() != expected.strip():
        return f"expected {expected.strip()!r}, got {ran.stdout.strip()!r}"
    return None


def check_invalid(src: pathlib.Path, expected: str) -> str | None:
    ll_path = src.with_suffix(".ll")
    compiled = run([sys.executable, str(ROOT / "compiler.py"), str(src), str(ll_path)])
    ll_path.unlink(missing_ok=True)
    if compiled.returncode == 0:
        return "expected compilation to fail, but it succeeded"
    if compiled.stderr.strip() != expected.strip():
        return f"expected {expected.strip()!r}, got {compiled.stderr.strip()!r}"
    return None


def main():
    failures = 0
    total = 0
    for src in sorted(TESTS_DIR.glob("*.txt")):
        expected_path = src.with_suffix(".expected")
        if not expected_path.exists():
            continue
        total += 1
        expected = expected_path.read_text()
        if src.name.startswith("valid_"):
            error = check_valid(src, expected)
        elif src.name.startswith("invalid_"):
            error = check_invalid(src, expected)
        else:
            continue
        if error:
            failures += 1
            print(f"FAIL {src.name}: {error}")
        else:
            print(f"PASS {src.name}")

    print(f"\n{total - failures}/{total} passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
