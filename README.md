# Languages and Compilers — Practice 5: if/else, scopes and basic blocks

A toy language with three types (`i32`, `i64`, `bool`) compiled to LLVM IR
via `llvmlite.ir`.

- `lexer.py` — Task 1 (Practice 2): a hand-written byte-level lexer state
  machine (`Token`, `lex`, `split_statements`). No regular expressions, no
  `str.split()`/`strtok`, no lexer generator. Practice 5 adds `if`,
  `else`, `while` as keywords and `!` as its own one-byte operator (not
  just the first half of `!=`); `{`/`}` are now plain tokens — the lexer
  no longer requires a `{` to close on the line it opened on, since a
  block's `{`/`}` live on their own lines and the parser pairs them.
- `grammar.ebnf` — the language in EBNF: `statement` gained `if` and
  `while`, both built from a new `block` rule (`"{" NL { statement }
  [ exit NL ] "}" NL`), `factor` gained the prefix `"!" factor`.
- `ast_nodes.py` — Task 1: the class hierarchy the grammar dictates
  (`ProgramNode`, `DeclNode`, `AssignNode`, `ExitNode`, `BinOpNode`,
  `VarNode`, `ConstNode`, `BoolNode`, plus Practice 5's `BlockNode`,
  `IfNode`, `WhileNode`, `NotNode`), and the `dump()` walk behind `--ast`.
- `parser.py` — a hand-written recursive-descent parser. Most statements
  are one line (`peek`/`eat` over `self.toks`), but `if`/`else`/`while`
  span several, so the parser keeps a **line cursor** next to the token
  cursor (`peek_line`/`next_line`) and `parse_block` walks lines itself
  until it finds the line that is just `}`. No parser generator, no
  regular expressions over the token list, no `eval`.
- `checker.py` — Task 2: `SemanticChecker`, a walk of its own that runs
  on the whole tree *before* any IR exists. The symbol table is now a
  **stack of frames** (`self.scopes`), one pushed per `Block` and popped
  on exit: a declaration is checked for a duplicate only in the top
  frame (so an outer name may be shadowed with any type), a use walks
  the frames from the top down. Decides the type of every expression and
  stores it on the node (`node.type`), resolves every name to its
  declaration (`node.decl`), and is the only place that rejects a
  program: declared once *per block*, declared before use, `mut` before
  `:=`, a `bool` condition for `if`/`while`, a `bool` under `!`, the
  Practice 4 type rules — each with `line:column`.
- `gen.py` — Task 3: `CodeGen`, a visitor that walks the *checked* tree
  and drives the `llvmlite.ir` builder API. It trusts `node.type` /
  `node.decl` and performs no checks of its own. A `Block` is a builder
  *position*, not a scope — `self.ptrs` is keyed by `DeclNode` identity,
  so shadowing costs nothing here either. An `if` becomes three basic
  blocks (`then`/`else`/`merge`) and one `cbranch`; a `while` becomes
  three (`while_cond`/`while_body`/`while_end`) with a branch back to
  the condition block. Every `alloca` still goes to the function's
  entry block (`builder.goto_entry_block()`), whichever block is current
  when the declaration is met, so `opt -passes=mem2reg` can promote it.
  Every basic block gets exactly one terminator; `exit` emits `printf`
  and `ret` wherever the builder stands. The only IR text produced is
  `str(module)`.
- `compiler.py` — wires lexer -> parser -> `SemanticChecker` ->
  `CodeGen` together and exposes the CLI (`--tokens`, `--ast`, or
  compile to `.ll`).
- `tests/ok/`, `tests/err/` — programs that must compile and run, and
  programs that must fail to compile, each with its expected
  stdout/stderr next to it, plus a runner (`tests/run_tests.py`).

## Language

One statement per line, three types: `i32` and `i64` are integers,
`bool` is a boolean with literals `true`/`false`. `if`/`else`/`while`
introduce blocks, which span several lines.

```
i32 mut a{10}
bool b{true}
if b
{
    a := a + 5
}
else
{
    a := a - 5
}
bool c{!b}                     # ! negates a bool
while a != 0
{
    a := a - 1
}
exit a                          # exit a | exit 42 | exit true — last line, exactly one
```

Rules:
- `+ - *` work on integers only; the result has the wider of the two
  types; `*` binds tighter, equal-precedence groups go left to right.
- `==`/`!=` compare two integers (any mix of widths) or two bools and
  produce a `bool`; one comparison per expression, binding weaker than
  arithmetic. `!` negates a `bool` and binds to the single factor right
  after it; `!a == b` compares `!a` with `b`.
- An `i32` value may initialise or be assigned to an `i64` variable;
  nothing else converts — an `i64` never narrows into an `i32`, a
  `bool` never meets an integer.
- A decimal constant gets the narrowest type it fits (`10` is `i32`,
  `3000000000` is `i64`); a constant too large for `i64` is a
  compile-time error.
- `if cond` / `else` / `while cond` stand alone on their line; `{`/`}`
  stand alone on theirs; `else` is optional on an `if`; a block holds
  at least one statement or a trailing `exit`. Blocks nest: `if` and
  `while` are statements, so one may stand inside another's block.
- A block pushes its own scope frame: a name is declared once per
  frame, before its first use within reach (the frames open at that
  point), and only a `mut` variable may be assigned. A name declared in
  an outer frame may be declared again inside a block with any type
  (shadowing); it reverts to the outer declaration once the block ends.
  Names are letters, digits and `_`, not starting with a digit; `i32`,
  `i64`, `bool`, `mut`, `exit`, `true`, `false`, `if`, `else`, `while`
  are reserved.
- A block may end with `exit`, as its last line; nothing may follow an
  `exit` in the same block (and nothing follows the program's last
  `exit`, which is mandatory and always the final line).
- Not in the language: parentheses; `/`, `<`, `>` or any operator other
  than `+ - * == != !`; a single `=`; a sign in front of a constant or
  a name; two comparisons in one expression; two statements on one
  line; a declaration without `{}`; an `if`/`while` condition that
  isn't `bool`; an empty block; `else` without a matching `if`.

## Running the compiler

```bash
python3 compiler.py input.txt output.ll   # compile
python3 compiler.py --ast input.txt        # print the tree, write nothing
lli output.ll                                # fastest way to check a change
llc -filetype=obj -relocation-model=pic output.ll -o output.o
clang -fPIE output.o -o program && ./program
opt -passes=mem2reg -S output.ll              # same IR, allocas promoted to registers/phi
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
