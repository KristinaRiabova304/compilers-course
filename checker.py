"""Task 2/3 (Practice 6): structs, functions, field chains and calls on
top of the Practice 5 semantic pass.

Walks the tree the parser produced, before any IR exists. Owns three
tables: structs (name -> StructNode, with field_index/field_type/field_mut
added here), functions (name -> FnNode, signature checked before any
body - "first collect all the signatures, then check and generate the
bodies"), and the variable scope stack (self.scopes, a frame per block).
Entering a function body swaps self.scopes for a fresh stack holding only
its parameters: the body does not see the top-level variables.

Decides the type of every expression and stores it on the node
(node.type); a var/field also stores its DeclNode/ParamNode and (for a
field) the gep index chain, a call stores the FnNode it resolved to -
gen.py reads these and looks nothing up by name itself. Rejects the
program with line:column.
"""
from ast_nodes import ConstNode, InitNode, ParamNode
from lexer import CompileError

INT_TYPES = ("i32", "i64")
BUILTIN_TYPES = ("i32", "i64", "bool")


class SemanticChecker:
    def __init__(self):
        self.structs = {}       # name -> StructNode
        self.functions = {}     # name -> FnNode
        self.scopes = [{}]      # stack of frames: name -> DeclNode/ParamNode
        self.current_fn = None  # FnNode while checking its body, else None

    def check(self, program):
        for struct in program.structs:
            self.declare_struct(struct)
        for fn in program.fns:
            self.declare_fn_signature(fn)
        for fn in program.fns:
            self.check_fn_body(fn)

        for stmt in program.statements:
            stmt.accept(self)
        program.exit.accept(self)

    def lookup(self, node, name):
        for frame in reversed(self.scopes):
            if name in frame:
                return frame[name]
        raise CompileError(node.line, node.col,
                            f"variable '{name}' is used before its declaration")

    # ---- structs ----

    def declare_struct(self, struct):
        if struct.name in self.structs or struct.name in BUILTIN_TYPES:
            raise CompileError(struct.line, struct.col,
                                f"struct '{struct.name}' is already declared")

        field_index, field_type, field_mut, field_order = {}, {}, {}, []
        for type_name, mutable, name, line, col in struct.fields:
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
        self.structs[struct.name] = struct

    def is_known_type(self, type_name):
        return type_name in BUILTIN_TYPES or type_name in self.structs

    # ---- functions ----

    def declare_fn_signature(self, fn):
        if fn.name in self.functions:
            raise CompileError(fn.line, fn.col, f"function '{fn.name}' is already declared")

        param_decls, seen = [], {}
        for type_name, name, line, col in fn.params:
            if not self.is_known_type(type_name):
                raise CompileError(line, col, f"unknown type '{type_name}'")
            if name in seen:
                raise CompileError(line, col,
                                    f"parameter '{name}' is already declared in '{fn.name}'")
            seen[name] = True
            param_decls.append(ParamNode(line, col, type_name, name))

        if not self.is_known_type(fn.result_type):
            raise CompileError(fn.line, fn.col, f"unknown result type '{fn.result_type}'")

        fn.param_decls = param_decls
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

    # ---- statements ----

    def visit_block(self, node):
        self.scopes.append({})
        for stmt in node.statements:
            stmt.accept(self)
        if node.exit:
            node.exit.accept(self)
        self.scopes.pop()

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
        gep index chain)."""
        base_decl = self.lookup(node, base_name)
        if require_mut and not base_decl.mutable:
            raise CompileError(node.line, node.col,
                                f"cannot assign to '{base_name}': it is not mut")

        current_type = base_decl.type_name
        indices = []
        for field_name in field_names:
            struct = self.structs.get(current_type)
            if struct is None or field_name not in struct.field_index:
                raise CompileError(node.line, node.col,
                                    f"'{current_type}' has no field '{field_name}'")
            if require_mut and not struct.field_mut[field_name]:
                raise CompileError(node.line, node.col,
                                    f"cannot assign through '{field_name}': it is not mut")
            indices.append(struct.field_index[field_name])
            current_type = struct.field_type[field_name]
        return base_decl, current_type, indices

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
        decl, final_type, indices = self.resolve_chain(
            node, node.name, node.fields, require_mut=True)
        node.decl, node.indices, node.type = decl, indices, final_type
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
        node.decl = self.lookup(node, node.name)
        node.type = node.decl.type_name
        return node.type

    def visit_field(self, node):
        decl, final_type, indices = self.resolve_chain(node, node.name, node.fields)
        node.decl, node.indices, node.type = decl, indices, final_type
        return node.type

    def visit_call(self, node):
        fn = self.functions.get(node.name)
        if fn is None:
            raise CompileError(node.line, node.col, f"function '{node.name}' is not declared")
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
            return
        if isinstance(expr, ConstNode) and have in INT_TYPES and want in INT_TYPES:
            raise CompileError(expr.line, expr.col, f"constant {expr.value} does not fit in {want}")
        raise CompileError(at.line, at.col,
                            f"cannot {what} of type {want} with a value of type {have}")
