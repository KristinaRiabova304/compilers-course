"""Runs every program in tests/ok and tests/err through compiler.py and
checks its output.

tests/ok/*.txt  must compile and, run through `lli`, print the matching
                *.expected on stdout.
tests/err/*.txt must fail to compile with the exact stderr line in the
                matching *.expected.
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS_DIR = pathlib.Path(__file__).resolve().parent


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def check_ok(src: pathlib.Path, expected: str) -> str | None:
    ll_path = src.with_suffix(".ll")
    compiled = run([sys.executable, str(ROOT / "compiler.py"), str(src), str(ll_path)])
    if compiled.returncode != 0:
        return f"compilation failed: {compiled.stderr.strip()}"

    ast_path = src.with_suffix(".ast")
    if ast_path.exists():
        dumped = run([sys.executable, str(ROOT / "compiler.py"), "--ast", str(src)])
        want_ast = ast_path.read_text().strip()
        if dumped.stdout.strip() != want_ast:
            return f"--ast: expected {want_ast!r}, got {dumped.stdout.strip()!r}"

    ran = run(["lli", str(ll_path)])
    ll_path.unlink(missing_ok=True)
    if ran.returncode != 0:
        return f"lli exited {ran.returncode}: {ran.stderr.strip()}"
    if ran.stdout.strip() != expected.strip():
        return f"expected {expected.strip()!r}, got {ran.stdout.strip()!r}"
    return None


def check_err(src: pathlib.Path, expected: str) -> str | None:
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
    for subdir, checker in (("ok", check_ok), ("err", check_err)):
        for src in sorted((TESTS_DIR / subdir).glob("*.txt")):
            expected_path = src.with_suffix(".expected")
            if not expected_path.exists():
                continue
            total += 1
            expected = expected_path.read_text()
            error = checker(src, expected)
            name = f"{subdir}/{src.name}"
            if error:
                failures += 1
                print(f"FAIL {name}: {error}")
            else:
                print(f"PASS {name}")

    print(f"\n{total - failures}/{total} passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
