# Languages and Compilers — Practice 2: a lexer as a state machine

A toy 32-bit-integer language compiled to LLVM IR via `llvmlite.ir`.

- `lexer.py` — Task 1: a hand-written byte-level lexer state machine
  (`Token`, `lex`, `split_statements`). No regular expressions, no
  `str.split()`/`strtok`, no lexer generator.
- `compiler.py` — Task 2: turns the token stream into declarations,
  assignments and `exit`, enforcing `mut` and declaration-before-use, and
  drives the same `llvmlite.ir` builder API used in Practice 1. The only
  IR text produced is `str(module)`.
- `tests/` — 6 programs that must compile and run, 6 that must fail, each
  with its expected stdout/stderr next to it, plus a runner.

## Language

One statement per line, every variable a 32-bit integer.

```
i32 x{5}              # const declaration, initialiser mandatory
i32 mut y{10}          # mutable declaration
i32 z{2 + 5}            # initialiser may be one operation on two operands
y := x                 # assignment, only allowed on a mut variable
y := x + 3
exit y                 # exit y | exit 42 — must be the last line
```

Variables must be declared once, before their first use. Names are
letters, digits and `_`, not starting with a digit; `i32`, `mut`, `exit`
are reserved.

## Running the compiler

```bash
python3 compiler.py input.txt output.ll
lli output.ll                                           # fastest way to check a change
llc -filetype=obj -relocation-model=pic output.ll -o output.o
clang -fPIE output.o -o program && ./program
```

Print the token list for a source file (used to eyeball the lexer):

```bash
python3 compiler.py --tokens input.txt
```

On any error the compiler writes one line to stderr and exits non-zero,
without writing the output file:

```
compilation error: line 2:8: unexpected byte '$'
```

## Running the tests

```bash
python3 tests/run_tests.py
```

Compiles every `tests/valid_*.txt`, runs it through `lli` and diffs stdout
against the matching `.expected` file; compiles every `tests/invalid_*.txt`
and diffs stderr against its `.expected` file. Requires `lli` on `PATH`
(part of LLVM, e.g. `brew install llvm`).

## Setup

```bash
pip3 install llvmlite
```
