"""Task 2: a semantic pass of its own.

Walks the tree the parser produced, before any IR exists. Owns the symbol
table (name -> DeclNode) and decides the type of every expression, storing
it on the node (node.type); a name also stores the declaration it resolved
to (node.decl). Rejects the program with line:column; the generator in
gen.py reads node.type/node.decl and looks nothing up itself.
"""
from ast_nodes import ConstNode
from lexer import CompileError

INT_TYPES = ("i32", "i64")


class SemanticChecker:
    def __init__(self):
        self.symbols = {}  # name -> DeclNode

    def check(self, program):
        program.accept(self)

    def visit_program(self, node):
        for stmt in node.statements:
            stmt.accept(self)
        node.exit.accept(self)

    def visit_decl(self, node):
        if node.name in self.symbols:
            raise CompileError(node.line, node.col, f"variable '{node.name}' is already declared")
        node.init.accept(self)  # before the name exists: no self-reference
        self.check_assignable(node.init, node.type_name, node, f"initialise '{node.name}'")
        self.symbols[node.name] = node

    def visit_assign(self, node):
        if node.name not in self.symbols:
            raise CompileError(node.line, node.col,
                                f"variable '{node.name}' is used before its declaration")
        decl = self.symbols[node.name]
        if not decl.mutable:
            raise CompileError(node.line, node.col, f"cannot assign to '{node.name}': it is not mut")
        node.decl = decl
        node.value.accept(self)
        self.check_assignable(node.value, decl.type_name, node, f"assign to '{node.name}'")

    def visit_exit(self, node):
        node.value.accept(self)

    def visit_binop(self, node):
        lt = node.left.accept(self)
        rt = node.right.accept(self)
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

    def visit_var(self, node):
        if node.name not in self.symbols:
            raise CompileError(node.line, node.col,
                                f"variable '{node.name}' is used before its declaration")
        node.decl = self.symbols[node.name]
        node.type = node.decl.type_name
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

    def check_assignable(self, expr, want, at, what):
        have = expr.type
        if have == want or (have == "i32" and want == "i64"):
            return
        if isinstance(expr, ConstNode) and have in INT_TYPES and want in INT_TYPES:
            raise CompileError(expr.line, expr.col, f"constant {expr.value} does not fit in {want}")
        raise CompileError(at.line, at.col,
                            f"cannot {what} of type {want} with a value of type {have}")
