"""Task 1 (Practice 7): member functions and member calls on top of the
Practice 6 semantic pass (structs, functions, field chains and calls).

Walks the tree the parser produced, before any IR exists. Owns three
tables: structs (name -> StructNode, with field_index/field_type/field_mut
and member_fns added here), functions (name -> FnNode, signature checked
before any body - "first collect all the signatures, then check and
generate the bodies"), and the variable scope stack (self.scopes, a frame
per block). Entering a function or member function body swaps self.scopes
for a fresh stack holding only its parameters: the body does not see the
top-level variables, though a member function's body additionally sees
its own struct's fields by their bare name - resolve_chain treats a name
that isn't a local/param as an implicit "self.name" when self.current_struct
is set, keyed by self.current_fn (the MemberFnNode) the same way gen.py's
compiler.py keys the receiver pointer.

Decides the type of every expression and stores it on the node
(node.type); a var/field also stores its DeclNode/ParamNode/MemberFnNode
and the gep index chain (empty for a plain var, one entry per '.' for a
field, also non-empty for an implicit self field), a call/member call
stores the FnNode/MemberFnNode it resolved to - gen.py reads these and
looks nothing up by name itself. Rejects the program with line:column.
"""
from ast_nodes import (
    ChainFieldNode, ConstNode, DeclNode, FieldNode, InitNode, LambdaNode, MemberFnNode,
    ParamNode, StringNode, StructNode, VarNode,
)
from lexer import CompileError

INT_TYPES = ("i32", "i64")
BUILTIN_TYPES = ("i32", "i64", "bool")
# "print" is a lexer keyword (never an identifier, so never a name), "read"
# is a plain identifier syntactically (read() parses through the ordinary
# call grammar) - it is reserved here instead, everywhere a name is
# introduced.
RESERVED_NAMES = ("read",)


class SemanticChecker:
    def __init__(self):
        self.structs = {}          # name -> StructNode
        self.functions = {}        # name -> FnNode
        self.scopes = [{}]         # stack of frames: name -> DeclNode/ParamNode
        self.current_fn = None     # FnNode/MemberFnNode while checking its body, else None
        self.current_struct = None  # the StructNode owning current_fn, only set for a member fn
        self.current_lambda_name = None       # set while checking a lambda's own body, else None
        self.current_lambda_captures = []     # that lambda's capture names, for the hint message
        self.lambda_name_counts = {}  # lambda name -> how many times it's been rewritten so far

    def check(self, program):
        self.pending_member_calls = []  # MemberCallNode, re-checked once mutating is known

        for struct in program.structs:
            self.declare_struct(struct)
        for fn in program.fns:
            self.declare_fn_signature(fn)

        for struct in program.structs:
            for member_fn in struct.fns:
                self.check_member_fn_body(member_fn, struct)
        for fn in program.fns:
            self.check_fn_body(fn)

        # a lambda statement (Practice 8 Task 2) is rewritten in place as
        # its statement list is walked - declare_struct also runs for its
        # generated struct, mid-walk, same as for every struct above; by
        # the time this finishes self.structs holds every struct in the
        # program, user-declared or generated, in a valid dependency order
        program.statements = self.check_statements(program.statements)
        program.exit.accept(self)

        self.compute_mutating()
        self.validate_pending_member_calls()

    def compute_mutating(self):
        """A member function is mutating if it assigns to a field of its
        object, or calls a mutating member function on its object or on
        one of its fields (the call graph gathered by visit_call/
        visit_member_call while checking every body). Member functions may
        call each other in a cycle, so this runs only after every body is
        checked, as a fixed point over the whole program: start from the
        ones that assign a field directly, then repeat marking callers of
        an already-mutating function until nothing new gets marked. Iterates
        self.structs (not program.structs) so a lambda's generated 'call'
        - never a top-level struct - is covered the same way."""
        all_fns = [fn for struct in self.structs.values() for fn in struct.fns]
        mutating = {fn for fn in all_fns if fn.direct_mutates}
        changed = True
        while changed:
            changed = False
            for fn in all_fns:
                if fn not in mutating and any(callee in mutating for callee in fn.calls):
                    mutating.add(fn)
                    changed = True
        for fn in all_fns:
            fn.is_mutating = fn in mutating

    def validate_pending_member_calls(self):
        """A mutating call is only allowed through a chain where every link
        - the object and each field on the way - is mut; non-mutating
        calls are fine through any chain. Deferred until every member
        function's is_mutating is final (see compute_mutating). node.obj
        has no .mut when it is itself a temporary (a call, a member call
        or a chain field, the additional task) rather than a named
        variable - "the result is a temporary copy: ... any member
        function can be called on it, also a mutating one", so that case
        is always fine, never checked. A call to a function literally
        named 'call' is always a callable object (Practice 8 Task 1,
        written directly as obj.call(...) or as sugar for obj(...)/
        obj.field(...)) - phrased after the object, not the method, same
        as the practice's own reference wording."""
        for node in self.pending_member_calls:
            obj_mut = getattr(node.obj, "mut", True)
            if node.fn.is_mutating and not obj_mut:
                chain = ".".join([node.obj.name, *getattr(node.obj, "fields", [])])
                if node.fn.name == "call":
                    raise CompileError(node.line, node.col,
                                        f"cannot call '{chain}': its 'call' changes it, "
                                        f"and it is not mut")
                raise CompileError(node.line, node.col,
                                    f"cannot call the mutating member function '{node.name}' "
                                    f"through '{chain}': it is not mut")

    def lookup_optional(self, name):
        for frame in reversed(self.scopes):
            if name in frame:
                return frame[name]
        return None

    def try_resolve_variable(self, name):
        """None, or (decl, type_name, indices, mut) for name resolved as a
        bare variable - a local/param, or (inside a member function) one
        of the receiver's own fields, the same two sources resolve_chain
        starts a chain from. Never raises: used by the call rule (Practice
        8 Task 1) to check whether a name is a visible variable without
        committing to it being one - a plain function call falls back to
        the normal name lookup when this returns None."""
        local = self.lookup_optional(name)
        if local is not None:
            return local, local.type_name, [], local.mutable
        if self.current_struct is not None and name in self.current_struct.field_index:
            struct = self.current_struct
            return (self.current_fn, struct.field_type[name],
                    [struct.field_index[name]], struct.field_mut[name])
        return None

    def check_not_reserved(self, name, line, col):
        if name in RESERVED_NAMES:
            raise CompileError(line, col, f"'{name}' is a reserved name")

    # ---- structs ----

    def declare_struct(self, struct):
        if struct.name in self.structs or struct.name in BUILTIN_TYPES:
            raise CompileError(struct.line, struct.col,
                                f"struct '{struct.name}' is already declared")

        field_index, field_type, field_mut, field_order = {}, {}, {}, []
        for type_name, mutable, name, line, col in struct.fields:
            self.check_not_reserved(name, line, col)
            if type_name == struct.name:
                raise CompileError(line, col,
                                    f"struct '{struct.name}' cannot contain itself")
            if type_name not in BUILTIN_TYPES and type_name not in self.structs:
                raise CompileError(line, col, f"unknown type '{type_name}'")
            if name in field_index:
                raise CompileError(line, col,
                                    f"field '{name}' is already declared in '{struct.name}'")
            field_index[name] = len(field_order)
            field_type[name] = type_name
            field_mut[name] = mutable
            field_order.append(name)

        struct.field_index, struct.field_type = field_index, field_type
        struct.field_mut, struct.field_order = field_mut, field_order
        # registered before member fn signatures: a member function may
        # take or return its own struct's type (passed/returned through a
        # pointer or by value, built from one it already has in hand) -
        # unlike a field, that is no size-recursion issue, only a field
        # cannot be of the struct's own type
        self.structs[struct.name] = struct

        member_fns = {}
        for member_fn in struct.fns:
            self.check_not_reserved(member_fn.name, member_fn.line, member_fn.col)
            if member_fn.name in field_index or member_fn.name in member_fns:
                raise CompileError(member_fn.line, member_fn.col,
                                    f"'{member_fn.name}' is already declared in '{struct.name}'")
            member_fn.param_decls = self.build_param_decls(member_fn.params, member_fn.name)
            if not self.is_known_type(member_fn.result_type):
                raise CompileError(member_fn.line, member_fn.col,
                                    f"unknown result type '{member_fn.result_type}'")
            # mutation-rule bookkeeping (compute_mutating/visit_assign/
            # visit_member_call): set here, not in check(), so a lambda's
            # generated struct (declared mid-walk, not up front) is
            # covered the same way as every other struct
            member_fn.direct_mutates = False
            member_fn.calls = []  # member fns this one calls on self/a field of self
            member_fns[member_fn.name] = member_fn
        struct.member_fns = member_fns

    def is_known_type(self, type_name):
        return type_name in BUILTIN_TYPES or type_name in self.structs

    # ---- functions ----

    def build_param_decls(self, params, owner_name):
        param_decls, seen = [], {}
        for type_name, name, line, col in params:
            self.check_not_reserved(name, line, col)
            if not self.is_known_type(type_name):
                raise CompileError(line, col, f"unknown type '{type_name}'")
            if name in seen:
                raise CompileError(line, col,
                                    f"parameter '{name}' is already declared in '{owner_name}'")
            seen[name] = True
            param_decls.append(ParamNode(line, col, type_name, name))
        return param_decls

    def declare_fn_signature(self, fn):
        self.check_not_reserved(fn.name, fn.line, fn.col)
        if fn.name in self.functions:
            raise CompileError(fn.line, fn.col, f"function '{fn.name}' is already declared")

        fn.param_decls = self.build_param_decls(fn.params, fn.name)
        if not self.is_known_type(fn.result_type):
            raise CompileError(fn.line, fn.col, f"unknown result type '{fn.result_type}'")

        self.functions[fn.name] = fn

    def check_fn_body(self, fn):
        if fn.body.exit is None:
            raise CompileError(fn.body.line, fn.body.col,
                                f"function '{fn.name}' must end with 'exit'")

        saved_scopes, saved_fn = self.scopes, self.current_fn
        self.scopes = [{p.name: p for p in fn.param_decls}]
        self.current_fn = fn
        fn.body.accept(self)
        self.scopes, self.current_fn = saved_scopes, saved_fn

    def check_member_fn_body(self, fn, struct):
        if fn.body.exit is None:
            raise CompileError(fn.body.line, fn.body.col,
                                f"member function '{struct.name}.{fn.name}' must end with 'exit'")

        saved_scopes, saved_fn, saved_struct = self.scopes, self.current_fn, self.current_struct
        self.scopes = [{p.name: p for p in fn.param_decls}]
        self.current_fn, self.current_struct = fn, struct
        fn.body.accept(self)
        self.scopes = saved_scopes
        self.current_fn, self.current_struct = saved_fn, saved_struct

    def rewrite_lambda(self, node):
        """Practice 8 Task 2: a lambda becomes three things the compiler
        already has - a struct with one field per capture (named
        'lambda.<name>', or 'lambda.<name>.2', '.3', ... for the second
        and later lambda that happens to share a name with an earlier one
        in a different scope - two lambdas can have the same name (each
        is only visible in its own scope, like any other local), but the
        generated struct lives in one flat, module-wide namespace, so a
        plain 'lambda.<name>' would collide the second time around; a dot
        can never appear in a program-declared name, so neither form ever
        clashes with one), that struct's own 'call' member function (the
        lambda's params/result/body), and a decl of that struct type,
        initialised with the captures' current values. After that, every
        call to the lambda's name is a callable-object call (Task 1) -
        gen.py needs nothing lambda-specific at all. Returns the generated
        DeclNode, for check_statements to insert right after this
        LambdaNode."""
        seen = {}
        for mutable, name, line, col in node.captures:
            if name in seen:
                raise CompileError(line, col, f"'{name}' is captured twice")
            seen[name] = True

        fields = []
        for mutable, name, line, col in node.captures:
            found = self.try_resolve_variable(name)
            if found is None:
                raise CompileError(line, col,
                                    f"cannot capture '{name}': no variable '{name}' "
                                    f"is visible here")
            _, type_name, _, _ = found
            fields.append((type_name, mutable, name, line, col))

        seen_count = self.lambda_name_counts.get(node.name, 0)
        self.lambda_name_counts[node.name] = seen_count + 1
        struct_name = f"lambda.{node.name}" if seen_count == 0 else \
            f"lambda.{node.name}.{seen_count + 1}"
        struct = StructNode(node.line, node.col, struct_name, fields, [])
        call_fn = MemberFnNode(node.line, node.col, "call", node.params,
                                node.result_type, node.body)
        struct.fns = [call_fn]
        struct.is_lambda, struct.lambda_name = True, node.name
        self.declare_struct(struct)
        node.struct = struct

        capture_names = {name for _, name, _, _ in node.captures}
        for param_decl in call_fn.param_decls:
            if param_decl.name in capture_names:
                raise CompileError(param_decl.line, param_decl.col,
                                    f"parameter '{param_decl.name}' is already a capture")

        saved_name = self.current_lambda_name
        saved_captures = self.current_lambda_captures
        self.current_lambda_name = node.name
        self.current_lambda_captures = [name for _, name, _, _ in node.captures]
        self.check_member_fn_body(call_fn, struct)
        self.current_lambda_name = saved_name
        self.current_lambda_captures = saved_captures

        init_items = [VarNode(line, col, name) for _, name, line, col in node.captures]
        # the additional task: the closure variable is mut when one of its
        # captures is mut, so a call like next() - a mutating member
        # function on next itself - works right where next was declared
        is_mut = any(mutable for mutable, _, _, _ in node.captures)
        decl_node = DeclNode(node.line, node.col, struct_name, node.name, is_mut,
                              InitNode(node.line, node.col, init_items))
        decl_node.accept(self)
        node.decl = decl_node
        return decl_node

    # ---- statements ----

    def visit_block(self, node):
        self.scopes.append({})
        node.statements = self.check_statements(node.statements)
        if node.exit:
            node.exit.accept(self)
        self.scopes.pop()

    def check_statements(self, statements):
        """Walks a list of statements (a block's, or the program's own),
        rewriting every LambdaNode (Practice 8 Task 2) into its generated
        struct and a decl of it (rewrite_lambda), inserted right after the
        LambdaNode itself - this is the one place a single parsed
        statement turns into two checked ones, so it returns a new list
        instead of annotating in place like every other visit_* does."""
        result = []
        for stmt in statements:
            if isinstance(stmt, LambdaNode):
                result.append(stmt)
                result.append(self.rewrite_lambda(stmt))
            else:
                stmt.accept(self)
                result.append(stmt)
        return result

    def visit_if(self, node):
        cond_type = node.condition.accept(self)
        if cond_type != "bool":
            raise CompileError(node.line, node.col,
                                f"the condition of 'if' must be bool, got {cond_type}")
        node.then_block.accept(self)
        if node.else_block:
            node.else_block.accept(self)

    def visit_while(self, node):
        cond_type = node.condition.accept(self)
        if cond_type != "bool":
            raise CompileError(node.line, node.col,
                                f"the condition of 'while' must be bool, got {cond_type}")
        node.body.accept(self)

    def visit_decl(self, node):
        self.check_not_reserved(node.name, node.line, node.col)
        if not self.is_known_type(node.type_name):
            raise CompileError(node.line, node.col, f"unknown type '{node.type_name}'")
        if node.name in self.scopes[-1]:
            raise CompileError(node.line, node.col,
                                f"variable '{node.name}' is already declared in this block")
        # before the name exists: no self-reference
        self.check_init(node.init, node.type_name, f"initialise '{node.name}'", at=node)
        node.type = node.type_name
        self.scopes[-1][node.name] = node

    def resolve_chain(self, node, base_name, field_names, require_mut=False):
        """Resolves base_name.field_names..., raising at the first link
        that is not mut when require_mut is set (the base object itself,
        or whichever field along the way). Returns (base_decl, final_type,
        gep index chain, chain_mut) - chain_mut is whether every link, base
        included, is mut, always computed (not just when require_mut is
        set) so a member call can check it later, once every member
        function's mutating status is known (visit_member_call defers that
        check - see validate_pending_member_calls). base_name need not be a
        local/param: inside a member function it may also be one of the
        receiver's own fields, named bare - resolved as if it started with
        an implicit 'self.', keyed by current_fn (the MemberFnNode),
        matching the pointer gen.py's compiler.py stores there."""
        found = self.try_resolve_variable(base_name)
        if found is None:
            if self.current_lambda_name is not None:
                hint = ", ".join([*self.current_lambda_captures, base_name])
                raise CompileError(node.line, node.col,
                                    f"'{base_name}' is not captured by lambda "
                                    f"'{self.current_lambda_name}': add it to the "
                                    f"capture list, [{hint}]")
            raise CompileError(node.line, node.col,
                                f"variable '{base_name}' is used before its declaration")
        base_decl, current_type, indices, base_mut = found

        if require_mut and not base_mut:
            # a non-mut capture needs its own wording ("the capture 'k' is
            # const, write [mut k]") - only when base_decl actually came
            # from the implicit-self/capture fallback (base_decl is
            # current_fn), never for an ordinary local/parameter
            if self.current_lambda_name is not None and base_decl is self.current_fn:
                raise CompileError(node.line, node.col,
                                    f"cannot assign to '{base_name}': the capture "
                                    f"'{base_name}' is const, write [mut {base_name}]")
            raise CompileError(node.line, node.col,
                                f"cannot assign to '{base_name}': it is not mut")

        chain_mut = base_mut
        for field_name in field_names:
            struct = self.structs.get(current_type)
            if struct is None or field_name not in struct.field_index:
                raise CompileError(node.line, node.col,
                                    f"'{current_type}' has no field '{field_name}'")
            if require_mut and not struct.field_mut[field_name]:
                raise CompileError(node.line, node.col,
                                    f"cannot assign through '{field_name}': it is not mut")
            chain_mut = chain_mut and struct.field_mut[field_name]
            indices.append(struct.field_index[field_name])
            current_type = struct.field_type[field_name]
        return base_decl, current_type, indices, chain_mut

    def is_fully_mut(self, type_name):
        """A built-in value is mut or not only through its own variable; a
        struct value is fully mut when every field is mut, at every depth -
        the rule ':=' needs to replace a whole object, not just a field."""
        if type_name in BUILTIN_TYPES:
            return True
        struct = self.structs[type_name]
        return all(struct.field_mut[f] and self.is_fully_mut(struct.field_type[f])
                   for f in struct.field_order)

    def visit_assign(self, node):
        decl, final_type, indices, _chain_mut = self.resolve_chain(
            node, node.name, node.fields, require_mut=True)
        node.decl, node.indices, node.type = decl, indices, final_type
        if self.current_fn is not None and decl is self.current_fn:
            self.current_fn.direct_mutates = True
        chain = ".".join([node.name, *node.fields])
        if not self.is_fully_mut(final_type):
            # := replaces final_type whole (a var or a field, whatever is on
            # the left), so every one of its own fields must be mut too -
            # not just the link we just walked through to reach it
            raise CompileError(node.line, node.col,
                                f"cannot assign to '{chain}': not every field of "
                                f"'{final_type}' is mut, at every depth")
        node.value.accept(self)
        self.check_assignable(node.value, final_type, node, f"assign to '{chain}'")

    def visit_exit(self, node):
        value_type = node.value.accept(self)
        if self.current_fn is None:
            self.check_not_lambda(node.value, "exit the program")
            if value_type not in BUILTIN_TYPES:
                raise CompileError(node.line, node.col,
                                    "the program's exit value must be a built-in type, "
                                    f"got {value_type}")
        else:
            self.check_assignable(node.value, self.current_fn.result_type, node,
                                   f"exit the function '{self.current_fn.name}'")

    # ---- expressions ----

    def visit_binop(self, node):
        lt = node.left.accept(self)
        rt = node.right.accept(self)
        if lt not in BUILTIN_TYPES or rt not in BUILTIN_TYPES:
            bad = lt if lt not in BUILTIN_TYPES else rt
            raise CompileError(node.line, node.col,
                                f"cannot apply '{node.op}' to a value of struct type {bad}")
        if node.op in ("+", "-", "*"):
            if lt == "bool" or rt == "bool":
                raise CompileError(node.line, node.col, f"cannot apply '{node.op}' to bool")
            node.type = "i64" if "i64" in (lt, rt) else "i32"
        else:
            if lt == "bool" or rt == "bool":
                if lt != rt:
                    other = rt if lt == "bool" else lt
                    raise CompileError(node.line, node.col, f"cannot compare bool with {other}")
            node.type = "bool"
        return node.type

    def visit_not(self, node):
        operand_type = node.operand.accept(self)
        if operand_type != "bool":
            raise CompileError(node.line, node.col, f"cannot apply '!' to {operand_type}")
        node.type = "bool"
        return node.type

    def visit_var(self, node):
        decl, final_type, indices, chain_mut = self.resolve_chain(node, node.name, [])
        node.decl, node.indices, node.type, node.mut = decl, indices, final_type, chain_mut
        return node.type

    def visit_field(self, node):
        decl, final_type, indices, chain_mut = self.resolve_chain(node, node.name, node.fields)
        node.decl, node.indices, node.type, node.mut = decl, indices, final_type, chain_mut
        return node.type

    def check_callable_call(self, node, obj, fn):
        """node.args against fn's params, shared by every way a call can
        reach a callable object's own 'call' (Practice 8 Task 1): obj is
        already resolved (its .type/.decl-or-not/.indices/.mut set, by
        try_resolve_variable for a bare name or by resolve_chain/
        visit_chain_field for a dotted one). Errors name the object
        (node.name), never 'call' itself, same as check_call_args below
        does for an explicit member call - node is a CallNode (the bare-
        name rewrite) or a MemberCallNode (the dotted-field rewrite),
        both already have the .line/.col/.name/.args this needs."""
        if len(node.args) != len(fn.params):
            raise CompileError(node.line, node.col,
                                f"'{node.name}' takes {len(fn.params)} argument(s), "
                                f"got {len(node.args)}")
        for i, (arg, param_decl) in enumerate(zip(node.args, fn.param_decls)):
            arg.accept(self)
            self.check_assignable(arg, param_decl.type_name, arg,
                                   f"pass argument {i + 1} to '{node.name}'")
        node.type = fn.result_type
        node.fn = fn
        node.obj = obj
        if self.current_fn is not None and getattr(obj, "decl", None) is self.current_fn:
            self.current_fn.calls.append(fn)
        self.pending_member_calls.append(node)

    def visit_call(self, node):
        """A bare f(...) inside a member function looks among the member
        functions of the same struct first (an implicit call on the same
        object, this.f(...) in all but name) and only then among the
        global functions - a member function hides a global one with the
        same name. read() is a builtin ahead of both: no struct or program
        function can ever be named 'read' (check_not_reserved), so this
        can never shadow or be shadowed by either.

        The call rule (Practice 8 Task 1): before any of that, a name
        followed by '(' might instead be a visible variable (a
        parameter/local, or a field inside a member function) whose type
        is a struct declaring 'call' - triple(14) is then sugar for
        triple.call(14), rewritten here into the same shape a direct
        member call produces (check_callable_call sets node.obj/.fn, so
        gen.py generates it exactly as if '.call' had been written). A
        name that is both such a variable and a function is an error at
        the call: there is no way to tell which one was meant."""
        if node.name == "read":
            if node.args:
                raise CompileError(node.line, node.col, "'read' takes no arguments")
            node.type, node.fn, node.on_self = "i32", None, False
            return node.type

        var = self.try_resolve_variable(node.name)
        is_member_fn = self.current_struct is not None and node.name in self.current_struct.member_fns
        is_global_fn = node.name in self.functions
        if var is not None and (is_member_fn or is_global_fn):
            raise CompileError(node.line, node.col,
                                f"'{node.name}' is both a variable and a function: "
                                f"rename one of them")

        if var is not None:
            decl, var_type, indices, mut = var
            struct = self.structs.get(var_type)
            call_fn = struct.member_fns.get("call") if struct is not None else None
            if call_fn is None:
                if var_type in BUILTIN_TYPES:
                    raise CompileError(node.line, node.col,
                                        f"'{node.name}' is not callable: it is {var_type}")
                raise CompileError(node.line, node.col,
                                    f"'{node.name}' is not callable: {var_type} has no "
                                    f"member function 'call'")
            obj = VarNode(node.line, node.col, node.name)
            obj.decl, obj.indices, obj.type, obj.mut = decl, indices, var_type, mut
            self.check_callable_call(node, obj, call_fn)
            return node.type

        if is_member_fn:
            fn = self.current_struct.member_fns[node.name]
            node.on_self = True
            self.current_fn.calls.append(fn)
        else:
            fn = self.functions.get(node.name)
            if fn is None:
                raise CompileError(node.line, node.col,
                                    f"function '{node.name}' is not declared")
            node.on_self = False
        if len(node.args) != len(fn.params):
            raise CompileError(node.line, node.col,
                                f"function '{node.name}' takes {len(fn.params)} argument(s), "
                                f"got {len(node.args)}")
        for i, (arg, param_decl) in enumerate(zip(node.args, fn.param_decls)):
            arg.accept(self)
            self.check_assignable(arg, param_decl.type_name, arg,
                                   f"pass argument {i + 1} to '{node.name}'")
        node.type = fn.result_type
        node.fn = fn
        return node.type

    def visit_member_call(self, node):
        obj_type = node.obj.accept(self)
        struct = self.structs.get(obj_type)
        if struct is None:
            raise CompileError(node.line, node.col,
                                f"cannot call '.{node.name}()' on a value of type {obj_type}")
        fn = struct.member_fns.get(node.name)
        if fn is None:
            # the call rule, after a dot (Practice 8 Task 1): node.name
            # might be a field of a callable struct type rather than a
            # member function - obj.name(args) is then sugar for
            # obj.name.call(args), the same rewrite as the bare-name case
            if node.name in struct.field_index:
                field_type = struct.field_type[node.name]
                field_struct = self.structs.get(field_type)
                call_fn = field_struct.member_fns.get("call") if field_struct is not None else None
                if call_fn is not None:
                    new_obj = self.extend_field_chain(node.obj, node.name, field_type,
                                                        struct.field_index[node.name])
                    self.check_callable_call(node, new_obj, call_fn)
                    return node.type
            raise CompileError(node.line, node.col,
                                f"'{obj_type}' has no member function '{node.name}'")
        if len(node.args) != len(fn.params):
            raise CompileError(node.line, node.col,
                                f"member function '{node.name}' takes {len(fn.params)} "
                                f"argument(s), got {len(node.args)}")
        for i, (arg, param_decl) in enumerate(zip(node.args, fn.param_decls)):
            arg.accept(self)
            self.check_assignable(arg, param_decl.type_name, arg,
                                   f"pass argument {i + 1} to '{node.name}'")
        node.type = fn.result_type
        node.fn = fn
        # node.obj has no .decl when it is itself a temporary (a call, a
        # member call or a chain field, the additional task) rather than a
        # named variable - getattr(..., None) never matches current_fn then
        if self.current_fn is not None and getattr(node.obj, "decl", None) is self.current_fn:
            # the object is self or a field reachable from self: this call
            # can make the enclosing member function itself mutating
            self.current_fn.calls.append(fn)
        self.pending_member_calls.append(node)
        return node.type

    def extend_field_chain(self, obj, field_name, field_type, field_index):
        """obj.field_name as an expression node, one step past whatever
        obj already was - a FieldNode (re-resolved through resolve_chain,
        so mut-chain tracking stays exact) when obj is itself addressable,
        a ChainFieldNode (extractvalue, the Practice 7 additional task)
        when obj is a temporary. Used only by the call rule's dotted-field
        rewrite (visit_member_call) to read the field a callable call
        actually targets."""
        if hasattr(obj, "decl"):
            fields = getattr(obj, "fields", []) + [field_name]
            new_obj = FieldNode(obj.line, obj.col, obj.name, fields)
            decl, final_type, indices, chain_mut = self.resolve_chain(new_obj, obj.name, fields)
            new_obj.decl, new_obj.indices, new_obj.type, new_obj.mut = (
                decl, indices, final_type, chain_mut)
            return new_obj
        new_obj = ChainFieldNode(obj.line, obj.col, obj, field_name)
        new_obj.index, new_obj.type = field_index, field_type
        return new_obj

    def visit_chain_field(self, node):
        """base.field, the additional task: base is a temporary (a call, a
        member call or another chain field), never a named variable -
        FieldNode/resolve_chain already cover that case."""
        base_type = node.base.accept(self)
        struct = self.structs.get(base_type)
        if struct is None:
            raise CompileError(node.line, node.col,
                                f"cannot read '.{node.field}' of a value of type {base_type}")
        if node.field not in struct.field_index:
            raise CompileError(node.line, node.col,
                                f"'{base_type}' has no field '{node.field}'")
        node.index = struct.field_index[node.field]
        node.type = struct.field_type[node.field]
        return node.type

    def visit_print(self, node):
        """print's argument is a string literal (nothing to check: it has
        no type) or any expr - any built-in or struct type it comes out to
        is printable, except a lambda's (Practice 8 Task 2 - its type has
        no name, so there is nothing to print it as)."""
        if not isinstance(node.arg, StringNode):
            node.arg.accept(self)
            self.check_not_lambda(node.arg, "print")

    def visit_const(self, node):
        if node.value < 2 ** 31:
            node.type = "i32"
        elif node.value < 2 ** 63:
            node.type = "i64"
        else:
            raise CompileError(node.line, node.col, f"constant {node.value} does not fit in i64")
        return node.type

    def visit_bool(self, node):
        node.type = "bool"
        return node.type

    # ---- initialisers ----

    def check_init(self, init, type_name, what, at=None):
        """Checks an InitNode against the expected type_name (built-in or
        struct), recursing into nested '{}' items (the additional task).
        what is a phrase like "initialise 'b'" or "initialise field 'radius'
        of 'Circle'", reused verbatim in any type-mismatch message here, so
        a plain built-in decl reads exactly as it did before structs
        existed; at is where that message points to (the DeclNode at the
        top level, this InitNode when nested). Stores init.type and
        init.whole_copy (struct case only) for gen.py."""
        at = at or init
        init.type = type_name

        if type_name in BUILTIN_TYPES:
            if len(init.items) != 1:
                raise CompileError(init.line, init.col,
                                    f"a value of type {type_name} needs exactly 1 value in "
                                    f"'{{}}', got {len(init.items)}")
            item = init.items[0]
            if isinstance(item, InitNode):
                raise CompileError(item.line, item.col,
                                    f"'{{}}' is not allowed for a value of type {type_name}")
            item.accept(self)
            self.check_assignable(item, type_name, at, what)
            return

        struct = self.structs[type_name]

        if len(init.items) == 1 and not isinstance(init.items[0], InitNode):
            item = init.items[0]
            item_type = item.accept(self)
            if item_type == type_name:
                init.whole_copy = True  # one value of the struct's own type: copies it whole
                return
            if len(struct.field_order) != 1:
                raise CompileError(init.line, init.col,
                                    f"struct '{type_name}' needs {len(struct.field_order)} "
                                    f"values in '{{}}', got 1")
            field_name = struct.field_order[0]
            self.check_assignable(item, struct.field_type[field_name], at, what)
            init.whole_copy = False
            return

        if len(init.items) != len(struct.field_order):
            raise CompileError(init.line, init.col,
                                f"struct '{type_name}' needs {len(struct.field_order)} "
                                f"values in '{{}}', got {len(init.items)}")

        init.whole_copy = False
        for item, field_name in zip(init.items, struct.field_order):
            field_type = struct.field_type[field_name]
            field_what = f"initialise field '{field_name}' of '{type_name}'"
            if isinstance(item, InitNode):
                if field_type in BUILTIN_TYPES:
                    raise CompileError(item.line, item.col,
                                        f"'{{}}' is not allowed for the built-in field "
                                        f"'{field_name}' of '{type_name}'")
                self.check_init(item, field_type, field_what)
            else:
                item.accept(self)
                self.check_assignable(item, field_type, init, field_what)

    def check_assignable(self, expr, want, at, what):
        have = expr.type
        if have == want or (have == "i32" and want == "i64"):
            # have == want lets one lambda capture another through here
            # (a capture field's declared type is exactly the captured
            # lambda's own type) - every other lambda misuse below always
            # has have != want, since no type a program can write is ever
            # a lambda's generated name
            return
        self.check_not_lambda(expr, what, at)
        if isinstance(expr, ConstNode) and have in INT_TYPES and want in INT_TYPES:
            raise CompileError(expr.line, expr.col, f"constant {expr.value} does not fit in {want}")
        raise CompileError(at.line, at.col,
                            f"cannot {what} of type {want} with a value of type {have}")

    def check_not_lambda(self, expr, what, at=None):
        """A lambda's generated struct type has no name in the language
        (Practice 8 Task 2): "no parameter, return type, variable or
        field can have it" - cannot be the value of exit, initialise a
        decl/field, be assigned, or passed as an argument (all through
        check_assignable, once have != want has already ruled out the one
        legitimate same-type case, capturing another lambda), or printed
        (visit_print calls this directly, there being no 'want' type to
        compare against there). at defaults to expr itself (most callers
        point at the value), but visit_assign/visit_exit point at the
        statement instead, matching every other check_assignable error."""
        struct = self.structs.get(expr.type)
        if struct is not None and getattr(struct, "is_lambda", False):
            at = at or expr
            name = getattr(expr, "name", struct.lambda_name)
            raise CompileError(at.line, at.col,
                                f"cannot {what}: '{name}' is a lambda, its type has no name")
