# Languages and Compilers — Practice 7: member functions

A toy language with three built-in types (`i32`, `i64`, `bool`),
user-defined `struct`s with fields and now member functions declared
inside them, plus the `read()`/`print()` builtins, compiled to LLVM IR
via `llvmlite.ir`.

- `lexer.py` — Practice 7 adds a `STRING` state: any byte except `"` and
  a newline, between two `"`, with no escape sequences; an unterminated
  one (newline or end of file first) is a lexer error at the opening
  `"`. `print` joins `KEYWORDS` (so a variable, field, parameter or
  function can never be named `print`, the same way none of them can be
  named `exit`). `read` stays a plain identifier — `read()` parses
  through the ordinary `call` rule like any other name — and is reserved
  only in `checker.py`, the one place a name is otherwise free to be
  anything.
- `grammar.ebnf` — `struct` gained `{ fn }` after its fields: a function
  declared inside a struct (a *member function*) reuses the top-level
  `fn` rule verbatim, just in a different place. `factor` gained
  `member_call` (`obj.f(...)`) and `chain` (the additional task — once a
  call has produced a value, `.` can keep going: `.field` reads a field
  of it, `.f(...)` calls a member function on it). `statement` gained
  `print`.
- `ast_nodes.py` — `StructNode` now also carries `fns`, a list of
  `MemberFnNode` (the same shape as `FnNode`) in declaration order,
  after every field. `MemberCallNode` (`obj`/`name`/`args` — `obj` is a
  `VarNode`/`FieldNode` for a named receiver, or itself a
  `CallNode`/`MemberCallNode`/`ChainFieldNode` for a temporary one, the
  additional task) and `ChainFieldNode` (`base`/`field`, reading a field
  of a temporary with no variable behind it to address) are new factors.
  `StringNode` and `PrintNode` back the new `print` statement.
- `parser.py` — a struct's body now parses its fields, then its member
  functions, in that order, rejecting a field after a member function
  and a member function before the first field, each at the exact
  token. `parse_factor`'s identifier branch reads a `.`-chain one name
  at a time: `(` right after a name ends the chain as a `member_call`
  (its object built from the names read so far), anything else keeps it
  as a field read. Every call or member call then runs through
  `parse_chain_tail` (the additional task): a further `.` continues
  reading fields or calling member functions on the result, with no
  type check at parse time — whether the result is actually a struct is
  `checker.py`'s job. `print` is a new statement, dispatched on the
  `print` keyword wherever any other statement can appear (top level, a
  function/member function body, `if`/`else`/`while`).
- `checker.py` — `resolve_chain` (shared by reading and writing a field
  chain, and now a bare variable too) resolves a name that isn't a
  local/parameter against the current member function's own struct, as
  if it had read `self.name` — a parameter or local of the same name
  still hides the field. A bare call `f(...)` inside a member function
  looks among that struct's own member functions before the program's
  global ones. A member function is *mutating* if it assigns to one of
  its own fields, or calls a mutating member function on itself or one
  of its fields; since member functions can call each other in a cycle,
  this is a fixed point computed once every body in the program has been
  checked, and only then is a mutating call's receiver chain checked for
  `mut` all the way through (a temporary produced by a chain is always
  fine to call a mutating function on — nothing else aliases it).
  `read()` is a builtin recognised ahead of any function lookup; `print`
  is already unusable as a name via the lexer, `read` is checked
  wherever any other name is introduced (field, member function,
  function, parameter, local).
- `gen.py` — a member function's receiver arrives as a plain pointer
  argument; `CodeGen.ptrs` keys it by the function's own `MemberFnNode`
  (no `alloca` needed, it is already an address), so the same
  `gep_chain` used for every other variable reaches its fields. `print`
  can appear in any function, not just the one that becomes `main`, so
  every `CodeGen` instance shares one runtime context (the module,
  `printf`, `scanf`, the format globals, a counter for unique string
  names) instead of the top level alone owning them; a global is never
  pre-bitcast — each use bitcasts fresh in whichever function's builder
  is current, since one function's instructions cannot be reused as a
  value in another. A chain's temporary (the additional task) is stored
  into a fresh `alloca` and its address passed as the receiver, exactly
  as calling a member function on a named variable would.
- `compiler.py` — a member function becomes an `ir.Function` named
  `Struct_method` (e.g. `Point_shift`, no `fn_` prefix — that one is
  only for top-level functions, to keep them out of `main`/`printf`'s
  way), its first parameter named `this`. Builds and shares the runtime
  context every `CodeGen` instance reads from.
- `tests/ok/`, `tests/err/` — programs that must compile and run, and
  programs that must fail to compile, each with its expected
  stdout/stderr next to it. A program that calls `read()` keeps its
  input in a `NAME.in` file next to `NAME.txt`; `tests/run_tests.py`
  feeds it to `lli` as standard input when present.

## Language

One statement per line. Three built-in types (`i32`, `i64`, `bool`)
plus `struct`s declared at the top of the file (fields, then member
functions) and `fn`s declared after them — a program is
`{ struct } { fn } { statement } exit`, in that order, always.

```
struct Point
{
    i32 mut x
    i32 mut y

    fn d2 := () -> i32
    {
        i32 r{x * x + y * y}
        exit r
    }

    fn moved := (i32 dx) -> Point
    {
        Point q{x + dx, y}
        exit q
    }
}

Point mut p{3, 4}
print("distance squared:")
print(p.d2())
i32 shifted{p.moved(1).d2()}
exit shifted
```

Rules on top of Practice 6 (structs, `fn` functions, field chains and
calls, which are unchanged):

- A struct's fields come first, then its member functions:
  `fn name := (T1 p1, ...) -> R { ... }`, declared and called exactly
  like a top-level function, except it is called *on an object*:
  `p.d2()`, never on the type itself (`Point.d2()` is an error). The
  object can be a field chain (`c.center.d2()`); a field after a member
  function, a member function before the first field, or a name shared
  between a field and a member function (or two member functions) are
  all rejected.
- Inside a member function's body, a bare name is looked up among its
  parameters and locals first, then among its own struct's fields — as
  if every field read `self.field`. A bare call looks among the same
  struct's member functions first, then the program's global functions.
  The object is passed by address, so writing a field (`x := x + dx`)
  is visible to the caller once the call returns, same as if it had
  written `p.x := p.x + dx` from outside.
- A member function is **mutating** if it writes one of its own fields,
  or calls a mutating member function on itself or one of its fields.
  A mutating call needs every link of its receiver chain `mut` — the
  object and each field on the way, the same rule as an assignment; a
  non-mutating call needs nothing. A chain's own temporary result
  (below) is always fine to call any member function on, mutating or
  not — it aliases nothing.
- `read()` reads one `i32` from standard input; it stands wherever a
  factor can. `print(arg)` is a statement — it has no value — printing
  its argument (an `i32`, an `i64`, a `bool`, a string literal written
  `"..."`, or a struct, printed field by field in declaration order)
  followed by a newline. Both names are reserved: no variable, field,
  parameter, function or member function may be named `read` or
  `print`.
- A call's result can be chained further when it is a struct: `.field`
  reads a field of it, `.f(...)` calls a member function on it —
  `p.moved(1).d2()` moves a copy of `p` and reads its own `d2()`,
  `p` itself unaffected. A `.` after a call whose result is not a
  struct is an error at that `.`; a chain can be read but never
  assigned to, since it names no variable.
- `x.y.z` still reads a field chain; `x.y.z := e` still writes through
  one, needing every link mut. One value of a struct's own type still
  copies the whole object; `:=` on a whole object still needs every
  field, at every depth, to be mut.
- Not in the language beyond Practice 6: grouping parentheses, a struct
  inside a struct it doesn't own (a member function's own parameter or
  result type is the one exception — it may be its struct's own type,
  seen only through a pointer or handed back by value), assigning to a
  call's result, a string literal anywhere but `print`'s argument, a
  member function called on anything but an object of the right type.

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

Compiles every `tests/ok/*.txt`, runs it through `lli` (feeding it
`NAME.in` as standard input when that file exists) and diffs stdout
against the matching `.expected` file; compiles every `tests/err/*.txt`
and diffs stderr against its `.expected` file. Requires `lli` on `PATH`
(part of LLVM, e.g. `brew install llvm`). Some `tests/ok/*` also have a
`.ast` file with the expected `--ast` dump next to them.

## Setup

```bash
pip3 install llvmlite
```
