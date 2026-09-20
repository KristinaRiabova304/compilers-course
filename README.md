# Languages and Compilers — Practice 3: a parser and an abstract syntax tree

A toy 32-bit-integer language compiled to LLVM IR via `llvmlite.ir`.

- `lexer.py` — Task 1 (Practice 2): a hand-written byte-level lexer state
  machine (`Token`, `lex`, `split_statements`). No regular expressions, no
  `str.split()`/`strtok`, no lexer generator.
- `grammar.ebnf` — Task 1: the language in EBNF, checked against the
  tokens the lexer emits.
- `ast_nodes.py` — Task 2: the class hierarchy the grammar dictates
  (`ProgramNode`, `DeclNode`, `AssignNode`, `ExitNode`, `BinOpNode`,
  `VarNode`, `ConstNode`), plus the `dump()` walk behind `--ast`.
- `parser.py` — Task 2 + Task 4: a hand-written recursive-descent parser
  (`peek`/`eat`, one method per grammar rule) that turns token vectors
  into the tree above. No parser generator, no regular expressions over
  the token list, no `eval`.
- `gen.py` — Task 3: `CodeGen`, a visitor that walks the tree and drives
  the same `llvmlite.ir` builder API as before. The only IR text produced
  is `str(module)`; declared-once, declared-before-use and mut-before-`:=`
  are checked during this walk.
- `compiler.py` — wires lexer → parser → `CodeGen` together and exposes
  the CLI (`--tokens`, `--ast`, or compile to `.ll`).
- `tests/` — 11 programs that must compile and run, 11 that must fail,
  each with its expected stdout/stderr next to it, plus a runner.

## Language

One statement per line, every variable a 32-bit integer. Task 4 widens
initialisers and `:=` to a full left-associative expression chain where
`*` binds tighter than `+`/`-`; `exit` still takes only a constant or a
variable.

```
i32 x{5}                    # const declaration, initialiser mandatory
i32 mut y{10}                # mutable declaration
i32 z{2 + 3 * 4}              # 14, not 20 — * binds tighter
i32 mut a{10 - 3 - 2}          # 5, not 9 — left associative
y := x                       # assignment, only allowed on a mut variable
a := a * 2 - 1
exit y                       # exit y | exit 42 — must be the last line
```

Variables must be declared once, before their first use. Names are
letters, digits and `_`, not starting with a digit; `i32`, `mut`, `exit`
are reserved. No parentheses, no `/`, no unary minus.

## Running the compiler

```bash
python3 compiler.py input.txt output.ll   # compile
python3 compiler.py --ast input.txt        # print the tree, write nothing
lli output.ll                                # fastest way to check a change
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
(part of LLVM, e.g. `brew install llvm`). Some `valid_*` tests also have a
`.ast` file with the expected `--ast` dump next to them.

## Setup

```bash
pip3 install llvmlite
```
