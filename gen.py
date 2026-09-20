from llvmlite import ir

from lexer import CompileError


class CodeGen:
    def __init__(self, builder, printf, fmt_ptr, i32):
        self.builder, self.printf, self.fmt_ptr, self.i32 = builder, printf, fmt_ptr, i32
        self.symbols = {}

    def visit_program(self, node):
        for stmt in node.statements:
            stmt.accept(self)
        node.exit.accept(self)

    def visit_decl(self, node):
        if node.name in self.symbols:
            raise CompileError(node.line, node.col, f"variable '{node.name}' is already declared")
        value = node.init.accept(self)
        ptr = self.builder.alloca(self.i32, name=node.name)
        self.builder.store(value, ptr)
        self.symbols[node.name] = {"mut": node.mutable, "ptr": ptr}

    def visit_assign(self, node):
        if node.name not in self.symbols:
            raise CompileError(node.line, node.col,
                                f"variable '{node.name}' is used before its declaration")
        if not self.symbols[node.name]["mut"]:
            raise CompileError(node.line, node.col, f"cannot assign to '{node.name}': it is not mut")
        value = node.value.accept(self)
        self.builder.store(value, self.symbols[node.name]["ptr"])

    def visit_exit(self, node):
        value = node.value.accept(self)
        self.builder.call(self.printf, [self.fmt_ptr, value])
        self.builder.ret(ir.Constant(self.i32, 0))

    def visit_binop(self, node):
        lv = node.left.accept(self)
        rv = node.right.accept(self)
        if node.op == "+":
            return self.builder.add(lv, rv)
        if node.op == "-":
            return self.builder.sub(lv, rv)
        return self.builder.mul(lv, rv)

    def visit_var(self, node):
        if node.name not in self.symbols:
            raise CompileError(node.line, node.col,
                                f"variable '{node.name}' is used before its declaration")
        return self.builder.load(self.symbols[node.name]["ptr"])

    def visit_const(self, node):
        return ir.Constant(self.i32, node.value)
