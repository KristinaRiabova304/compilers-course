class Node:
    def __init__(self, line, col):
        self.line, self.col = line, col


class ProgramNode(Node):
    def __init__(self, line, col, statements, exit_node):
        super().__init__(line, col)
        self.statements, self.exit = statements, exit_node

    def accept(self, visitor):
        return visitor.visit_program(self)

    def dump(self, indent=0):
        print("  " * indent + "Program")
        for stmt in self.statements:
            stmt.dump(indent + 1)
        self.exit.dump(indent + 1)


class StmtNode(Node):
    pass


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
    def __init__(self, line, col, name, value):
        super().__init__(line, col)
        self.name, self.value = name, value

    def accept(self, visitor):
        return visitor.visit_assign(self)

    def dump(self, indent=0):
        print("  " * indent + f"Assign {self.name}")
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
