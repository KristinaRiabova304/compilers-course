class Node:
    def __init__(self, line, col):
        self.line, self.col = line, col


class ProgramNode(Node):
    def __init__(self, line, col, structs, fns, statements, exit_node):
        super().__init__(line, col)
        self.structs, self.fns = structs, fns
        self.statements, self.exit = statements, exit_node

    def accept(self, visitor):
        return visitor.visit_program(self)

    def dump(self, indent=0):
        print("  " * indent + "Program")
        for s in self.structs:
            s.dump(indent + 1)
        for f in self.fns:
            f.dump(indent + 1)
        for stmt in self.statements:
            stmt.dump(indent + 1)
        self.exit.dump(indent + 1)


class StructNode(Node):
    """fields: list of (type_name, mutable, name, line, col) in declaration
    order; line/col point at the field's type token."""

    def __init__(self, line, col, name, fields):
        super().__init__(line, col)
        self.name, self.fields = name, fields

    def dump(self, indent=0):
        print("  " * indent + f"Struct {self.name}")
        for type_name, mutable, name, _line, _col in self.fields:
            print("  " * (indent + 1)
                  + f"Field {name} {type_name} {'mut' if mutable else 'const'}")


class FnNode(Node):
    """params: list of (type_name, name, line, col). body: BlockNode."""

    def __init__(self, line, col, name, params, result_type, body):
        super().__init__(line, col)
        self.name, self.params, self.result_type, self.body = name, params, result_type, body

    def dump(self, indent=0):
        params = ", ".join(f"{t} {n}" for t, n, _line, _col in self.params)
        print("  " * indent + f"Fn {self.name}({params}) -> {self.result_type}")
        self.body.dump(indent + 1)


class ParamNode(Node):
    """Stands in for a function parameter in the var-scope frame and in
    CodeGen.ptrs, the same way a DeclNode stands for a local variable -
    parameters are never reassigned, so mutable is always False."""

    def __init__(self, line, col, type_name, name):
        super().__init__(line, col)
        self.type_name, self.name, self.mutable = type_name, name, False


class StmtNode(Node):
    pass


class InitNode(Node):
    """One '{' expr-or-nested-'{}' { ',' expr-or-nested-'{}' } '}'. Each
    item is either an ExprNode or another InitNode (nested braces, the
    additional task)."""

    def __init__(self, line, col, items):
        super().__init__(line, col)
        self.items = items

    def accept(self, visitor):
        return visitor.visit_init(self)

    def dump(self, indent=0):
        print("  " * indent + "Init")
        for item in self.items:
            item.dump(indent + 1)


class DeclNode(StmtNode):
    def __init__(self, line, col, type_name, name, mutable, init):
        super().__init__(line, col)
        self.type_name, self.name, self.mutable, self.init = type_name, name, mutable, init

    def accept(self, visitor):
        return visitor.visit_decl(self)

    def dump(self, indent=0):
        print("  " * indent
              + f"Decl {self.name} {self.type_name} {'mut' if self.mutable else 'const'}")
        self.init.dump(indent + 1)


class AssignNode(StmtNode):
    """name is the base identifier, fields is the (possibly empty) list of
    trailing '.' field names: 'x := e' has fields == [], 'x.y.z := e' has
    fields == ['y', 'z']."""

    def __init__(self, line, col, name, fields, value):
        super().__init__(line, col)
        self.name, self.fields, self.value = name, fields, value

    def accept(self, visitor):
        return visitor.visit_assign(self)

    def dump(self, indent=0):
        chain = ".".join([self.name, *self.fields])
        print("  " * indent + f"Assign {chain}")
        self.value.dump(indent + 1)


class ExitNode(Node):
    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value

    def accept(self, visitor):
        return visitor.visit_exit(self)

    def dump(self, indent=0):
        print("  " * indent + "Exit")
        self.value.dump(indent + 1)


class BlockNode(Node):
    """A '{' ... '}' body: zero or more statements, an optional trailing
    exit. Not itself a statement - If and While hold one or two of these."""

    def __init__(self, line, col, statements, exit_node):
        super().__init__(line, col)
        self.statements, self.exit = statements, exit_node

    def accept(self, visitor):
        return visitor.visit_block(self)

    def dump(self, indent=0):
        print("  " * indent + "Block")
        for stmt in self.statements:
            stmt.dump(indent + 1)
        if self.exit:
            self.exit.dump(indent + 1)


class IfNode(StmtNode):
    def __init__(self, line, col, condition, then_block, else_block):
        super().__init__(line, col)
        self.condition, self.then_block, self.else_block = condition, then_block, else_block

    def accept(self, visitor):
        return visitor.visit_if(self)

    def dump(self, indent=0):
        print("  " * indent + "If")
        self.condition.dump(indent + 1)
        self.then_block.dump(indent + 1)
        if self.else_block:
            self.else_block.dump(indent + 1)


class WhileNode(StmtNode):
    def __init__(self, line, col, condition, body):
        super().__init__(line, col)
        self.condition, self.body = condition, body

    def accept(self, visitor):
        return visitor.visit_while(self)

    def dump(self, indent=0):
        print("  " * indent + "While")
        self.condition.dump(indent + 1)
        self.body.dump(indent + 1)


class ExprNode(Node):
    pass


class BinOpNode(ExprNode):
    def __init__(self, line, col, op, left, right):
        super().__init__(line, col)
        self.op, self.left, self.right = op, left, right

    def accept(self, visitor):
        return visitor.visit_binop(self)

    def dump(self, indent=0):
        print("  " * indent + f"BinOp {self.op}")
        self.left.dump(indent + 1)
        self.right.dump(indent + 1)


class VarNode(ExprNode):
    def __init__(self, line, col, name):
        super().__init__(line, col)
        self.name = name

    def accept(self, visitor):
        return visitor.visit_var(self)

    def dump(self, indent=0):
        print("  " * indent + f"Var {self.name}")


class FieldNode(ExprNode):
    """name is the base identifier, fields is the non-empty list of
    trailing '.' field names: 'p.x.y' -> name='p', fields=['x', 'y']."""

    def __init__(self, line, col, name, fields):
        super().__init__(line, col)
        self.name, self.fields = name, fields

    def accept(self, visitor):
        return visitor.visit_field(self)

    def dump(self, indent=0):
        print("  " * indent + f"Field {'.'.join([self.name, *self.fields])}")


class CallNode(ExprNode):
    def __init__(self, line, col, name, args):
        super().__init__(line, col)
        self.name, self.args = name, args

    def accept(self, visitor):
        return visitor.visit_call(self)

    def dump(self, indent=0):
        print("  " * indent + f"Call {self.name}")
        for arg in self.args:
            arg.dump(indent + 1)


class ConstNode(ExprNode):
    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value

    def accept(self, visitor):
        return visitor.visit_const(self)

    def dump(self, indent=0):
        print("  " * indent + f"Const {self.value}")


class BoolNode(ExprNode):
    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value

    def accept(self, visitor):
        return visitor.visit_bool(self)

    def dump(self, indent=0):
        print("  " * indent + f"Bool {'true' if self.value else 'false'}")


class NotNode(ExprNode):
    def __init__(self, line, col, operand):
        super().__init__(line, col)
        self.operand = operand

    def accept(self, visitor):
        return visitor.visit_not(self)

    def dump(self, indent=0):
        print("  " * indent + "Not")
        self.operand.dump(indent + 1)
