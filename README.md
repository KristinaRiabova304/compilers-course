# Languages and Compilers — Practice 6: structs and functions

A toy language with three built-in types (`i32`, `i64`, `bool`) plus
user-defined `struct`s, and now `fn` functions, compiled to LLVM IR via
`llvmlite.ir`.

- `lexer.py` — Task 1 (Practice 2): a hand-written byte-level lexer state
  machine (`Token`, `lex`, `split_statements`). No regular expressions, no
  `str.split()`/`strtok`, no lexer generator. Practice 6 adds `struct`
  and `fn` to `KEYWORDS`, and five new operator tokens: `.` and `,` are
  single-byte like `+`/`*`; `(`/`)` get their own `"paren"` token kind
  (a call's parentheses must never be mistaken for a block's `{`/`}`);
  `->` needs one byte of lookahead to tell it from a bare `-`, so `-`
  gets its own `MINUS` state, the same trick already used for `:=`,
  `==`/`!=` and `!`.
- `grammar.ebnf` — the language in EBNF: `program` now orders
  `{ struct } { fn } { statement } exit`, `struct`/`fn`/`field_decl` are
  new top-level rules, `decl`'s single `expr` becomes an `init_value`
  list (a value or a nested `{ ... }`, the additional task), `factor`
  gained `field` and `call`, `assign` gained an optional `.` chain.
- `ast_nodes.py` — the class hierarchy the grammar dictates: `StructNode`
  (fields as `(type_name, mutable, name, line, col)` tuples, filled in by
  the checker with `field_index`/`field_type`/`field_mut`/`field_order`),
  `FnNode` (params as tuples, body a `BlockNode`), `ParamNode` (stands in
  for a parameter the same way `DeclNode` stands in for a local variable
  — `mutable` is always `False`), `InitNode` (a decl's `{ ... }`, items
  possibly nested `InitNode`s), `FieldNode` and `CallNode` for the new
  factors, and `AssignNode` now carries a `fields` chain.
- `parser.py` — a hand-written recursive-descent parser, line cursor
  (`peek_line`/`next_line`) next to the token cursor, as in Practice 5.
  `parse_program` tracks a `structs -> fns -> statements` phase and
  rejects a struct/fn that shows up after a later phase has started.
  `parse_statement`'s `identifier` branch now needs one token of
  lookahead (`peek_next`) to tell `TypeName name{...}` (a decl with a
  struct type) from `name := ...`/`name.field := ...` (an assign) from
  `name(...)` on its own (a call alone is not a statement — rejected
  right there). `parse_factor`'s `identifier` branch needs the same
  lookahead for `name(...)` (a call) vs `name.field...` (a field chain)
  vs a bare `name` (a variable). `parse_init`/`parse_init_item` recurse
  for nested `{}` (the additional task); no parser generator, no
  regular expressions over the token list, no `eval`.
- `checker.py` — Task 2/3: `SemanticChecker`, extended with two more
  tables alongside the Practice 5 scope stack: `self.structs` (name ->
  `StructNode`, field index/type/mut/order resolved here — *the
  semantic pass finds the index*) and `self.functions` (name -> `FnNode`,
  every signature collected before any body is checked, so a function
  may call itself or one declared below it). Checking a function body
  swaps `self.scopes` for a fresh stack holding only its parameters —
  the body never sees the top-level variables. `resolve_chain` walks a
  `.`-chain once for both a read (`visit_field`) and a write
  (`visit_assign`), raising at the first non-mut link when the write
  needs one; `is_fully_mut` recurses through a struct's fields (and
  their struct fields) to decide whether `:=` may replace it whole.
  `check_init` validates a decl's `{ ... }` field by field against the
  struct (or the single value against a built-in type), recursing into
  nested `{}`. Still the only place that rejects a program, with
  `line:column`; `gen.py` reads `node.type`/`node.decl`/`node.indices`/
  `node.fn` and looks nothing up by name itself.
- `gen.py` — Task 1/2/3: `CodeGen`, now built once per function body
  (and once for the top-level statements, which live in `main`) since
  each function needs its own `builder`/`self.ptrs`. `is_top_level`
  picks whether `exit` prints and returns 0 (the program ending) or
  coerces its value to the function's result type and `ret`s it. A
  field is reached with one `gep` whose index list is the chain
  `resolve_chain` already resolved (`gep_chain`) — chaining the indices
  in one instruction walks through nested structs without an
  intermediate pointer. An object is an `alloca` of a named struct type;
  a struct value passed by value, returned, or copied whole is loaded
  from one `alloca` and stored into another (or passed/returned
  directly — a first-class aggregate, no byref). A nested `{}` (the
  additional task) builds one aggregate value bottom-up with chained
  `insert_value`, never touching memory until the whole value is ready
  to store. The only IR text produced is `str(module)`.
- `compiler.py` — wires lexer -> parser -> `SemanticChecker` ->
  `CodeGen` together. Builds every struct as a named identified type
  (`module.context.get_identified_type(name).set_body(...)`) in
  declaration order, so a later struct's fields already find their
  type; declares every function's `ir.Function` (name prefixed `fn_` so
  a program function called `main` or `printf` cannot clash with the
  runtime) before generating any body, mirroring the checker's
  signatures-then-bodies order; then generates each function's body and
  finally `main`'s.
- `tests/ok/`, `tests/err/` — programs that must compile and run, and
  programs that must fail to compile, each with its expected
  stdout/stderr next to it, plus a runner (`tests/run_tests.py`).

## Language

One statement per line. Three built-in types (`i32`, `i64`, `bool`)
plus `struct`s declared at the top of the file and `fn`s declared after
them — a program is `{ struct } { fn } { statement } exit`, in that
order, always.

```
struct Point
{
    i32 mut x
    i32 mut y
}
struct Circle
{
    Point center
    i32 mut radius
}

fn moved := (Point p, i32 dx) -> Point
{
    Point q{p.x + dx, p.y}
    exit q
}

Point p{10, 20}
Circle c{p, 5}
i32 xCoord{c.center.x}
Point mut r{moved(p, 1)}
r.x := r.x + 1
exit r.x
```

Rules on top of Practice 5 (if/else/while/blocks/scope frames/`!`,
which are unchanged):

- A struct field is a built-in type or a struct declared **above** it
  (never itself, directly or through another struct); a field is
  constant unless marked `mut`. An object is declared like a variable,
  one value per field in order: `Point p{10, 20}`. A field that is
  itself a struct takes a value of that type (a variable or a call) —
  or, written inline, a nested `{}` built the same way, as deep as the
  struct nests: `Circle c{{10, 20}, 5}`. One value of the struct's own
  type copies the whole object: `Point q{p}`.
- `x.y.z` reads a field chain; `x.y.z := e` writes through one. A write
  needs **every** link mut — the variable and every field named on the
  way — one non-mut link anywhere rejects it, even if the ends are mut.
  Replacing a whole object with `:=` (no `.` chain) additionally needs
  every field of its type, at every depth, to be mut — a struct with
  even one non-mut field can never be replaced whole, only field by
  field.
- `fn name := (T1 p1, T2 p2) -> R { ... }` declares a function: any
  number of parameters (built-in or struct), a result type (built-in
  or struct). Parameters are copies, never reassignable; the body sees
  only its own parameters and locals and every function of the program
  (by name — a function may call itself or one declared below it),
  never the top-level variables. The body is a block whose last line is
  `exit`; inside a function `exit` ends the function with its value (the
  function's result type, exactly or `i32`-into-`i64` widened); an
  `exit` inside an `if` ends the function early. At the top level `exit`
  still ends the program and its value must be a built-in type.
- `name(a, b)` calls a function; the number and types of the arguments
  must match the parameters (the same widening rule). A call is a
  factor — it can stand wherever a field or a variable can — but never
  alone as a statement.
- Not in the language beyond Practice 5: grouping parentheses (`(`/`)`
  exist only around a call's arguments or a function's parameters), a
  struct inside a struct it doesn't own, a struct or a function
  declared after a statement, a function or a struct nested inside a
  function body, a `{}` written for a built-in field, a `{}` with the
  wrong number of values.

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
compilation error: line 7:6: 'Point' has no field 'z'
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
