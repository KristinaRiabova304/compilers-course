"""Runs every program in tests/ok, tests/err and tests/opt through
compiler.py and checks its output.

tests/ok/*.txt  must compile and, run through `lli`, print the matching
                *.expected on stdout.
tests/err/*.txt must fail to compile with the exact stderr line in the
                matching *.expected.
tests/opt/*.txt (Practice 9) must print the matching *.expected both with
                and without --remove-unused, and --remove-unused
                --ast-checked must match the matching *.astc tree.

--remove-unused on this script's own command line (or REMOVE_UNUSED=1 in
the environment) re-runs every tests/ok and tests/err program with that
flag added to every compiler.py invocation - the optimizer must change
nothing an earlier practice already tested: every program still prints
the same, every error is still the same error.
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS_DIR = pathlib.Path(__file__).resolve().parent


def run(cmd, stdin_text=None):
    return subprocess.run(cmd, capture_output=True, text=True, input=stdin_text)


def check_ok(src: pathlib.Path, expected: str, extra_flags=()) -> str | None:
    ll_path = src.with_suffix(".ll")
    compiled = run([sys.executable, str(ROOT / "compiler.py"), *extra_flags, str(src), str(ll_path)])
    if compiled.returncode != 0:
        return f"compilation failed: {compiled.stderr.strip()}"

    ast_path = src.with_suffix(".ast")
    if ast_path.exists():
        dumped = run([sys.executable, str(ROOT / "compiler.py"), "--ast", str(src)])
        want_ast = ast_path.read_text().strip()
        if dumped.stdout.strip() != want_ast:
            return f"--ast: expected {want_ast!r}, got {dumped.stdout.strip()!r}"

    in_path = src.with_suffix(".in")
    stdin_text = in_path.read_text() if in_path.exists() else None
    ran = run(["lli", str(ll_path)], stdin_text=stdin_text)
    ll_path.unlink(missing_ok=True)
    if ran.returncode != 0:
        return f"lli exited {ran.returncode}: {ran.stderr.strip()}"
    if ran.stdout.strip() != expected.strip():
        return f"expected {expected.strip()!r}, got {ran.stdout.strip()!r}"
    return None


def check_err(src: pathlib.Path, expected: str, extra_flags=()) -> str | None:
    ll_path = src.with_suffix(".ll")
    ll_path.unlink(missing_ok=True)
    compiled = run([sys.executable, str(ROOT / "compiler.py"), *extra_flags, str(src), str(ll_path)])
    wrote_ll = ll_path.exists()
    ll_path.unlink(missing_ok=True)
    if compiled.returncode == 0:
        return "expected compilation to fail, but it succeeded"
    if wrote_ll:
        return "compilation failed but still wrote the .ll file"
    if compiled.stderr.strip() != expected.strip():
        return f"expected {expected.strip()!r}, got {compiled.stderr.strip()!r}"
    return None


def check_opt(src: pathlib.Path, expected: str) -> str | None:
    """Must print the same thing with and without --remove-unused, and
    the optimized tree (--remove-unused --ast-checked) must match the
    recorded .astc, when there is one."""
    in_path = src.with_suffix(".in")
    stdin_text = in_path.read_text() if in_path.exists() else None

    for flags in ([], ["--remove-unused"]):
        ll_path = src.with_suffix(".ll")
        compiled = run([sys.executable, str(ROOT / "compiler.py"), *flags, str(src), str(ll_path)])
        if compiled.returncode != 0:
            return f"compilation failed ({flags or ['no flags']}): {compiled.stderr.strip()}"
        ran = run(["lli", str(ll_path)], stdin_text=stdin_text)
        ll_path.unlink(missing_ok=True)
        if ran.returncode != 0:
            return f"lli exited {ran.returncode} ({flags or ['no flags']}): {ran.stderr.strip()}"
        if ran.stdout.strip() != expected.strip():
            return f"{flags or ['no flags']}: expected {expected.strip()!r}, got {ran.stdout.strip()!r}"

    astc_path = src.with_suffix(".astc")
    if astc_path.exists():
        dumped = run([sys.executable, str(ROOT / "compiler.py"), "--remove-unused",
                      "--ast-checked", str(src)])
        want_astc = astc_path.read_text().strip()
        if dumped.stdout.strip() != want_astc:
            return f"--ast-checked: expected {want_astc!r}, got {dumped.stdout.strip()!r}"
    return None


def main():
    extra_flags = ["--remove-unused"] if (
        "--remove-unused" in sys.argv[1:] or os.environ.get("REMOVE_UNUSED")) else []

    failures = 0
    total = 0
    for subdir, checker in (("ok", check_ok), ("err", check_err)):
        for src in sorted((TESTS_DIR / subdir).glob("*.txt")):
            expected_path = src.with_suffix(".expected")
            if not expected_path.exists():
                continue
            total += 1
            expected = expected_path.read_text()
            error = checker(src, expected, extra_flags)
            name = f"{subdir}/{src.name}"
            if error:
                failures += 1
                print(f"FAIL {name}: {error}")
            else:
                print(f"PASS {name}")

    for src in sorted((TESTS_DIR / "opt").glob("*.txt")):
        expected_path = src.with_suffix(".expected")
        if not expected_path.exists():
            continue
        total += 1
        expected = expected_path.read_text()
        error = check_opt(src, expected)
        name = f"opt/{src.name}"
        if error:
            failures += 1
            print(f"FAIL {name}: {error}")
        else:
            print(f"PASS {name}")

    print(f"\n{total - failures}/{total} passed" + (" (with --remove-unused)" if extra_flags else ""))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
