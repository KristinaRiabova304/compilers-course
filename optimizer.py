"""Task 1/2 (Practice 9): the first optimization pass - removes unused
variable declarations and unused global functions from the checked tree,
before code generation ever sees it. Runs only when --remove-unused is
given; without the flag the tree it gets is exactly what the checker
produced, untouched.

Two visitors:
- UseCounter walks the whole tree once and counts, per declaration (keyed
  by the DeclNode object itself, not by name - the checker already
  resolved every name to the one declaration it means, and reusing that
  link is the only way two declarations named x stay distinct) how many
  times it is read, and per top-level function (keyed by FnNode) how many
  times it is called. A declaration whose initialiser (or an assignment's
  value) contains an effectful call or read() is always counted as read
  at least once, even with zero real reads: dropping it would change what
  the program prints. (Without the additional task every call would count
  as "might print"; with it - see compute_effects below - only a call
  that provably has an effect does, so a call of a pure function no
  longer keeps a value alive by itself.)
- Pruner rebuilds every block's statement list and the top-level function
  list, dropping a declaration with zero reads (and every assignment to
  it) and a function with zero calls, except a lambda's own generated
  declaration, parameters and struct fields - none of those are ever
  removed (a parameter belongs to the function's signature and its
  callers pass it regardless; a lambda is not "in scope" of the pass at
  all, so neither is any capture its kept declaration still reads).

The additional task - calls without effects:
- EffectWalker walks one function's own body (never descending into a
  nested lambda's separate body) and collects whether it directly prints
  or reads, whether it has a while (may not return), and which other
  functions it calls (an edge in the call graph, used both for the
  "calls an effectful function" fixpoint and for cycle detection - a
  function that can reach itself through one or more calls may also not
  return, the same reason a call that never returns is observable).
- compute_effects(program) builds this for every function/member
  function/lambda 'call' reachable in the tree (gathering the ones
  nested inside a lambda the same way UseCounter does, lazily, as each
  level is discovered) and returns the fixpoint set: start from the
  functions with a direct effect (print/read, mutating, or recursive),
  then add any function that calls one already in the set, until nothing
  changes. A struct member function's own .is_mutating (Practice 7/8's
  mutation rule) is reused as is - "a mutating member function: it
  changes the object it is called on" is exactly that flag, including
  for a callable object's own 'call' and a lambda's.
- This set is computed once, before remove_unused's loop, not once per
  round: removing a declaration or a function changes call counts (and
  so what Task 2 can remove next), but never changes any surviving
  function's own body or what it calls, so no function's effect status
  can change partway through - only reachability (Task 2) does.

remove_unused(program) alternates declaration and function removal until
a round removes nothing: removing a declaration can make another
declaration's only use disappear (y was the only read of x) or take away
a function's only remaining call (the additional task); removing a
function can make another function's only call disappear. Both kinds of
removal are decided from one counting pass and applied together, so there
is one loop, not two. The loop terminates because every round that does
not stop removes at least one declaration or function, of which there are
finitely many.
"""
from ast_nodes import (
    AssignNode, BinOpNode, CallNode, ChainFieldNode, DeclNode, FnNode, IfNode,
    InitNode, LambdaNode, MemberCallNode, NotNode, StringNode, WhileNode,
)


def expr_has_effect(expr, effects):
    """Whether expr contains read() or a call of a function in effects,
    anywhere within it - including a call's own object (a chained
    temporary, Practice 7's additional task, can itself hold a further
    call) and arguments, since evaluating expr runs those too."""
    if isinstance(expr, (CallNode, MemberCallNode)):
        if getattr(expr, "fn", None) is None:
            return True  # read()
        if expr.fn in effects:
            return True
        obj = getattr(expr, "obj", None)
        if obj is not None and expr_has_effect(obj, effects):
            return True
        return any(expr_has_effect(arg, effects) for arg in expr.args)
    if isinstance(expr, BinOpNode):
        return expr_has_effect(expr.left, effects) or expr_has_effect(expr.right, effects)
    if isinstance(expr, NotNode):
        return expr_has_effect(expr.operand, effects)
    if isinstance(expr, ChainFieldNode):
        return expr_has_effect(expr.base, effects)
    return False


def init_has_effect(init, effects):
    """Same as expr_has_effect, for a decl's InitNode: each item is either
    a plain expr or another InitNode (a nested '{}', field by field)."""
    for item in init.items:
        if isinstance(item, InitNode):
            if init_has_effect(item, effects):
                return True
        elif expr_has_effect(item, effects):
            return True
    return False


class EffectWalker:
    """Walks one function's own body - never descending into a nested
    lambda's body, which is a separate function visited on its own (see
    compute_effects) - collecting what the additional task's rules need:
    does the body itself print or read(), does it have a while (may not
    return), and which other functions does it call (edges for both the
    "calls an effectful function" fixpoint and cycle detection)."""

    def __init__(self):
        self.prints_or_reads = False
        self.has_while = False
        self.calls = set()
        self.lambdas = []  # nested LambdaNode 'call' MemberFnNodes found

    # ---- statements ----

    def visit_block(self, node):
        for stmt in node.statements:
            stmt.accept(self)
        if node.exit:
            node.exit.accept(self)

    def visit_if(self, node):
        node.condition.accept(self)
        node.then_block.accept(self)
        if node.else_block:
            node.else_block.accept(self)

    def visit_while(self, node):
        self.has_while = True
        node.condition.accept(self)
        node.body.accept(self)

    def visit_decl(self, node):
        node.init.accept(self)

    def visit_init(self, node):
        for item in node.items:
            item.accept(self)

    def visit_assign(self, node):
        node.value.accept(self)

    def visit_exit(self, node):
        node.value.accept(self)

    def visit_print(self, node):
        self.prints_or_reads = True
        if not isinstance(node.arg, StringNode):
            node.arg.accept(self)

    def visit_lambda(self, node):
        self.lambdas.append(node.struct.fns[0])  # visited separately, not here

    # ---- expressions ----

    def visit_binop(self, node):
        node.left.accept(self)
        node.right.accept(self)

    def visit_not(self, node):
        node.operand.accept(self)

    def visit_var(self, node):
        pass

    def visit_field(self, node):
        pass

    def visit_chain_field(self, node):
        node.base.accept(self)

    def visit_call(self, node):
        if node.fn is None:
            self.prints_or_reads = True  # read()
            return
        self.calls.add(node.fn)
        if getattr(node, "obj", None) is not None:
            node.obj.accept(self)
        for arg in node.args:
            arg.accept(self)

    def visit_member_call(self, node):
        self.calls.add(node.fn)
        node.obj.accept(self)
        for arg in node.args:
            arg.accept(self)

    def visit_const(self, node):
        pass

    def visit_bool(self, node):
        pass


def is_recursive(fn, calls_graph):
    """Whether fn can reach itself through one or more calls, directly or
    through others - a cycle in the call graph through fn."""
    stack = list(calls_graph.get(fn, ()))
    seen = set()
    while stack:
        current = stack.pop()
        if current is fn:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(calls_graph.get(current, ()))
    return False


def find_top_level_lambdas(statements):
    """A lambda declared directly among the top-level statements (or
    inside a top-level if/while) is never inside any function's own body,
    so EffectWalker.visit_lambda never finds it by walking from program.fns
    - this is the one place compute_effects needs its own small walk, just
    to seed the pending queue with lambdas that have no "outer" function."""
    found = []
    for stmt in statements:
        if isinstance(stmt, LambdaNode):
            found.append(stmt.struct.fns[0])
        elif isinstance(stmt, IfNode):
            found.extend(find_top_level_lambdas(stmt.then_block.statements))
            if stmt.else_block:
                found.extend(find_top_level_lambdas(stmt.else_block.statements))
        elif isinstance(stmt, WhileNode):
            found.extend(find_top_level_lambdas(stmt.body.statements))
    return found


def compute_effects(program):
    """Every function/member function/lambda 'call' reachable in the
    tree, classified as having an effect or not, as a fixpoint: a
    function has a direct effect if it prints or reads, is a mutating
    member function, or may not return (a while, or a recursion cycle);
    it inherits one by calling a function that already has one."""
    pending = list(program.fns)
    for struct in program.structs:
        pending.extend(struct.fns)
    pending.extend(find_top_level_lambdas(program.statements))

    all_fns, walked = [], {}
    while pending:
        fn = pending.pop()
        if fn in walked:
            continue
        walker = EffectWalker()
        fn.body.accept(walker)
        walked[fn] = walker
        all_fns.append(fn)
        pending.extend(walker.lambdas)

    calls_graph = {fn: walker.calls for fn, walker in walked.items()}

    effects = set()
    for fn in all_fns:
        walker = walked[fn]
        if (walker.prints_or_reads or walker.has_while
                or getattr(fn, "is_mutating", False) or is_recursive(fn, calls_graph)):
            effects.add(fn)

    changed = True
    while changed:
        changed = False
        for fn in all_fns:
            if fn not in effects and any(callee in effects for callee in calls_graph[fn]):
                effects.add(fn)
                changed = True
    return effects


class UseCounter:
    def __init__(self, effects):
        self.effects = effects  # functions a call of which may not be dropped
        self.reads = {}  # DeclNode -> count
        self.calls = {}  # FnNode -> count

    def read(self, decl):
        self.reads[decl] = self.reads.get(decl, 0) + 1

    def call(self, fn):
        self.calls[fn] = self.calls.get(fn, 0) + 1

    # ---- statements ----

    def visit_program(self, node):
        for stmt in node.statements:
            stmt.accept(self)
        node.exit.accept(self)

    def visit_block(self, node):
        for stmt in node.statements:
            stmt.accept(self)
        if node.exit:
            node.exit.accept(self)

    def visit_if(self, node):
        node.condition.accept(self)
        node.then_block.accept(self)
        if node.else_block:
            node.else_block.accept(self)

    def visit_while(self, node):
        node.condition.accept(self)
        node.body.accept(self)

    def visit_decl(self, node):
        self.reads.setdefault(node, 0)
        if init_has_effect(node.init, self.effects):
            self.read(node)  # has an effect: kept regardless of real reads
        node.init.accept(self)

    def visit_init(self, node):
        for item in node.items:
            item.accept(self)

    def visit_assign(self, node):
        # the target (node.name/node.fields) is never a read - only the
        # value is walked, and only it can force the target's decl alive
        if expr_has_effect(node.value, self.effects):
            self.read(node.decl)
        node.value.accept(self)

    def visit_exit(self, node):
        node.value.accept(self)

    def visit_print(self, node):
        if not isinstance(node.arg, StringNode):
            node.arg.accept(self)

    def visit_lambda(self, node):
        # the lambda itself is never in scope, but its body is: a local
        # declaration inside it can still be pruned like any other
        node.struct.fns[0].body.accept(self)

    # ---- expressions ----

    def visit_binop(self, node):
        node.left.accept(self)
        node.right.accept(self)

    def visit_not(self, node):
        node.operand.accept(self)

    def visit_var(self, node):
        self.read(node.decl)

    def visit_field(self, node):
        self.read(node.decl)  # "at the start of a field chain p.x"

    def visit_chain_field(self, node):
        node.base.accept(self)

    def visit_call(self, node):
        if node.fn is None:  # read()
            return
        if isinstance(node.fn, FnNode):  # never a MemberFnNode (on_self/callable)
            self.call(node.fn)
        if getattr(node, "obj", None) is not None:
            node.obj.accept(self)  # "as a callable object in a call c(5)"
        for arg in node.args:
            arg.accept(self)

    def visit_member_call(self, node):
        node.obj.accept(self)  # "as the object of a member call p.sum()"
        for arg in node.args:
            arg.accept(self)

    def visit_const(self, node):
        pass

    def visit_bool(self, node):
        pass


class Pruner:
    def __init__(self, dead_decls, dead_fns):
        self.dead_decls = dead_decls
        self.dead_fns = dead_fns

    def prune(self, program):
        program.fns = [fn for fn in program.fns if fn not in self.dead_fns]
        for fn in program.fns:
            fn.body.statements = self.prune_statements(fn.body.statements)
        for struct in program.structs:
            for member_fn in struct.fns:
                member_fn.body.statements = self.prune_statements(member_fn.body.statements)
        program.statements = self.prune_statements(program.statements)

    def is_dead_decl(self, decl):
        return decl in self.dead_decls and not getattr(decl, "from_lambda", False)

    def prune_statements(self, statements):
        """Rebuilds the list rather than deleting from the one being
        walked. A statement that is neither a dead decl nor a dead
        assignment is kept as is, except If/While/Lambda, whose own
        nested blocks are pruned in place first - the statement itself
        (and, for If, its empty-block case) is never dropped here, only
        declarations and assignments are."""
        result = []
        for stmt in statements:
            if isinstance(stmt, DeclNode) and self.is_dead_decl(stmt):
                continue
            if isinstance(stmt, AssignNode) and self.is_dead_decl(stmt.decl):
                continue
            if isinstance(stmt, IfNode):
                stmt.then_block.statements = self.prune_statements(stmt.then_block.statements)
                if stmt.else_block:
                    stmt.else_block.statements = self.prune_statements(stmt.else_block.statements)
            elif isinstance(stmt, WhileNode):
                stmt.body.statements = self.prune_statements(stmt.body.statements)
            elif isinstance(stmt, LambdaNode):
                lambda_fn = stmt.struct.fns[0]
                lambda_fn.body.statements = self.prune_statements(lambda_fn.body.statements)
            result.append(stmt)
        return result


def remove_unused(program):
    # computed once: removing a declaration or a function changes call
    # counts, never a surviving function's own body or what it calls, so
    # no function's effect status can change partway through the loop
    effects = compute_effects(program)

    while True:
        counter = UseCounter(effects)
        for struct in program.structs:
            for member_fn in struct.fns:
                member_fn.body.accept(counter)
        for fn in program.fns:
            fn.body.accept(counter)
        program.accept(counter)

        # a lambda's own generated decl is never a candidate (Pruner would
        # refuse to remove it anyway) - excluded here too, not just there,
        # so it never keeps showing up at zero reads and stalling the loop
        # forever: an unreachable lambda is always "dead" by the count,
        # but it is never actually removed, so it must never count towards
        # "this round removed something"
        dead_decls = {decl for decl, n in counter.reads.items()
                      if n == 0 and not getattr(decl, "from_lambda", False)}
        dead_fns = {fn for fn in program.fns if counter.calls.get(fn, 0) == 0}
        if not dead_decls and not dead_fns:
            return program
        Pruner(dead_decls, dead_fns).prune(program)
