# Languages and Compilers — Practice 4: types, comparisons and a semantic pass

A toy language with three types (`i32`, `i64`, `bool`) compiled to LLVM IR
via `llvmlite.ir`.

- `lexer.py` — Task 1 (Practice 2): a hand-written byte-level lexer state
  machine (`Token`, `lex`, `split_statements`). No regular expressions, no
  `str.split()`/`strtok`, no lexer generator. Practice 4 adds `i64`,
  `bool`, `true`, `false` as keywords and `==`/`!=` as two-byte operators
  (a bare `=` or `!` is a lexical error).
- `grammar.ebnf` — the language in EBNF: `decl` now carries a `type`,
  `expr` gained one optional comparison over the old `+ - *` chain
  (renamed `arith`), `factor` gained `true`/`false`.
- `ast_nodes.py` — Task 1: the class hierarchy the grammar dictates
  (`ProgramNode`, `DeclNode`, `AssignNode`, `ExitNode`, `BinOpNode`,
  `VarNode`, `ConstNode`, `BoolNode`), plus the `dump()` walk behind
  `--ast`. `DeclNode` now carries `type_name`.
- `parser.py` — a hand-written recursive-descent parser (`peek`/`eat`,
  one method per grammar rule) that turns token vectors into the tree
  above. No parser generator, no regular expressions over the token
  list, no `eval`.
- `checker.py` — Task 2: `SemanticChecker`, a walk of its own that runs
  on the whole tree *before* any IR exists. Owns the symbol table
  (name -> `DeclNode`), decides the type of every expression and stores
  it on the node (`node.type`), resolves every name to its declaration
  (`node.decl`), and is the only place that rejects a program: declared
  once, declared before use, `mut` before `:=`, the type rules below —
  each with `line:column`.
- `gen.py` — Task 3: `CodeGen`, a visitor that walks the *checked* tree
  and drives the `llvmlite.ir` builder API. It trusts `node.type` /
  `node.decl` and performs no checks of its own: an `i64` variable is an
  `alloca i64`, a `bool` is an `i1`, and every widening the checker
  allowed becomes an explicit `sext i32 -> i64` via one `coerce()`
  helper. Comparisons become `icmp` after widening to a common width.
  The only IR text produced is `str(module)`.
- `compiler.py` — wires lexer -> parser -> `SemanticChecker` ->
  `CodeGen` together and exposes the CLI (`--tokens`, `--ast`, or
  compile to `.ll`).
- `tests/ok/`, `tests/err/` — programs that must compile and run, and
  programs that must fail to compile, each with its expected
  stdout/stderr next to it, plus a runner (`tests/run_tests.py`).

## Language

One statement per line, three types: `i32` and `i64` are integers,
`bool` is a boolean with literals `true`/`false`.

```
i64 a{10}                     # 64-bit integer
bool b{true}                  # boolean, mandatory initialiser
i32 mut x{15}                 # mutable, 32-bit
i64 mut y{x}                  # i32 -> i64 widens on initialisation
bool c{a == 10}                # == / != compare two integers or two bools
bool mut d{a != 10}
d := b == c
y := y * x + a                 # + - * : integers only, result is the wider type
exit y                         # exit y | exit 42 | exit true — last line, exactly one
```

Rules:
- `+ - *` work on integers only; the result has the wider of the two
  types; `*` binds tighter, equal-precedence groups go left to right.
- `==`/`!=` compare two integers (any mix of widths) or two bools and
  produce a `bool`; one comparison per expression, binding weaker than
  arithmetic.
- An `i32` value may initialise or be assigned to an `i64` variable;
  nothing else converts — an `i64` never narrows into an `i32`, a
  `bool` never meets an integer.
- A decimal constant gets the narrowest type it fits (`10` is `i32`,
  `3000000000` is `i64`); a constant too large for `i64` is a
  compile-time error.
- A variable is declared once, before its first use, and only a `mut`
  variable may be assigned. Names are letters, digits and `_`, not
  starting with a digit; `i32`, `i64`, `bool`, `mut`, `exit`, `true`,
  `false` are reserved.
- Not in the language: parentheses; `/`, `<`, `>` or any operator other
  than `+ - * == !=`; a single `=` or `!`; a sign in front of a
  constant or a name; two comparisons in one expression; two statements
  on one line; a declaration without `{}`; anything after `exit`.

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
compilation error: line 2:5: cannot initialise 'c' of type i32 with a value of type i64
```

## Running the tests

```bash
python3 tests/run_tests.py
```

Compiles every `tests/ok/*.txt`, runs it through `lli` and diffs stdout
against the matching `.expected` file; compiles every `tests/err/*.txt`
and diffs stderr against its `.expected` file. Requires `lli` on `PATH`
(part of LLVM, e.g. `brew install llvm`). Some `tests/ok/*` also have a
`.ast` file with the expected `--ast` dump next to them.

## Setup

```bash
pip3 install llvmlite
```
