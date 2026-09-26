"""Task 3: code generation from a checked tree.

The SemanticChecker has already run: every expression node carries
node.type, every VarNode/AssignNode carries node.decl. This walk looks
nothing up and raises nothing - it only builds llvmlite.ir instructions,
widening with sext at exactly the five places the checker allowed it.
"""
from llvmlite import ir


class CodeGen:
    def __init__(self, builder, printf, fmt_int, fmt_bool, str_true, str_false, i32, i64, i1):
        self.builder = builder
        self.printf = printf
        self.fmt_int, self.fmt_bool = fmt_int, fmt_bool
        self.str_true, self.str_false = str_true, str_false
        self.i32, self.i64, self.i1 = i32, i64, i1
        self.ir_types = {"i32": i32, "i64": i64, "bool": i1}
        self.ptrs = {}  # name -> alloca ptr

    def ir_type(self, type_name):
        return self.ir_types[type_name]

    def coerce(self, value, have, want):
        if have == "i32" and want == "i64":
            return self.builder.sext(value, self.i64, name="wide")
        return value

    def visit_program(self, node):
        for stmt in node.statements:
            stmt.accept(self)
        node.exit.accept(self)

    def visit_decl(self, node):
        value = node.init.accept(self)
        value = self.coerce(value, node.init.type, node.type_name)
        ptr = self.builder.alloca(self.ir_type(node.type_name), name=node.name)
        self.builder.store(value, ptr)
        self.ptrs[node.name] = ptr

    def visit_assign(self, node):
        value = node.value.accept(self)
        value = self.coerce(value, node.value.type, node.decl.type_name)
        self.builder.store(value, self.ptrs[node.name])

    def visit_exit(self, node):
        value = node.value.accept(self)
        if node.value.type == "bool":
            chosen = self.builder.select(value, self.str_true, self.str_false, name="exit_str")
            self.builder.call(self.printf, [self.fmt_bool, chosen])
        else:
            value = self.coerce(value, node.value.type, "i64")
            self.builder.call(self.printf, [self.fmt_int, value])
        self.builder.ret(ir.Constant(self.i32, 0))

    def visit_binop(self, node):
        lv, rv = node.left.accept(self), node.right.accept(self)
        lt, rt = node.left.type, node.right.type

        if node.op in ("+", "-", "*"):
            lv, rv = self.coerce(lv, lt, node.type), self.coerce(rv, rt, node.type)
            if node.op == "+":
                return self.builder.add(lv, rv)
            if node.op == "-":
                return self.builder.sub(lv, rv)
            return self.builder.mul(lv, rv)

        if lt != "bool":
            width = "i64" if "i64" in (lt, rt) else "i32"
            lv, rv = self.coerce(lv, lt, width), self.coerce(rv, rt, width)
        predicate = "==" if node.op == "==" else "!="
        return self.builder.icmp_signed(predicate, lv, rv)

    def visit_var(self, node):
        return self.builder.load(self.ptrs[node.name])

    def visit_const(self, node):
        return ir.Constant(self.ir_type(node.type), node.value)

    def visit_bool(self, node):
        return ir.Constant(self.i1, 1 if node.value else 0)
