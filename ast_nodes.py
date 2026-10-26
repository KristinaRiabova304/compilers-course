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
    order; line/col point at the field's type token. fns: list of
    MemberFnNode, in declaration order, every one of them after every
    field."""

    def __init__(self, line, col, name, fields, fns):
        super().__init__(line, col)
        self.name, self.fields, self.fns = name, fields, fns

    def dump(self, indent=0):
        print("  " * indent + f"Struct {self.name}")
        for type_name, mutable, name, _line, _col in self.fields:
            print("  " * (indent + 1)
                  + f"Member {name} {type_name} {'mut' if mutable else 'const'}")
        for fn in self.fns:
            fn.dump(indent + 1)


class FnNode(Node):
    """params: list of (type_name, name, line, col). body: BlockNode."""

    def __init__(self, line, col, name, params, result_type, body):
        super().__init__(line, col)
        self.name, self.params, self.result_type, self.body = name, params, result_type, body

    def dump(self, indent=0):
        params = ", ".join(f"{t} {n}" for t, n, _line, _col in self.params)
        print("  " * indent + f"Fn {self.name}({params}) -> {self.result_type}")
        self.body.dump(indent + 1)


class MemberFnNode(Node):
    """A struct's own fn: same shape as FnNode (params/result_type/body),
    held on StructNode.fns instead of Program.fns. Its body implicitly
    sees the struct's own fields by their bare name - the receiver, passed
    by pointer so a field write through it is visible to the caller."""

    def __init__(self, line, col, name, params, result_type, body):
        super().__init__(line, col)
        self.name, self.params, self.result_type, self.body = name, params, result_type, body

    def dump(self, indent=0):
        print("  " * indent + f"MemberFn {self.name} -> {self.result_type}")
        for type_name, name, _line, _col in self.params:
            print("  " * (indent + 1) + f"Param {name} {type_name}")
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


class StringNode(Node):
    """A string literal: only ever print's argument, never a factor, so it
    has no accept() - visit_print reads node.arg.value directly instead of
    walking it through the expression visitor."""

    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value

    def dump(self, indent=0):
        print("  " * indent + f'String "{self.value}"')


class PrintNode(StmtNode):
    """print(arg): a statement, not a factor - it has no value. arg is a
    StringNode or any ExprNode."""

    def __init__(self, line, col, arg):
        super().__init__(line, col)
        self.arg = arg

    def accept(self, visitor):
        return visitor.visit_print(self)

    def dump(self, indent=0):
        print("  " * indent + "Print")
        self.arg.dump(indent + 1)


class LambdaNode(StmtNode):
    """fn name := [captures](params) -> type NL block, a function declared
    where a statement stands, together with copies of the variables it
    needs (Practice 8 Task 2). captures: list of (mutable, name, line,
    col) - mutable is the additional task, [mut name]; params: tuples
    like a FnNode's. The semantic pass rewrites every lambda into a
    struct (one field per capture) with a 'call' member function (this
    lambda's own params/result/body) plus a decl of that struct type,
    capturing the current values - checker.py sets .struct (and .decl)
    once it has them, for --ast-checked to show; --ast, the parse tree,
    never does. gen.py's visit_lambda is a no-op: the struct/member
    function are generated the same way any other is (by walking every
    struct the checker knows about, not just the parsed ones), and the
    capturing decl right after this node in the statement list does the
    rest, through the ordinary DeclNode path."""

    def __init__(self, line, col, name, captures, params, result_type, body):
        super().__init__(line, col)
        self.name, self.captures = name, captures
        self.params, self.result_type, self.body = params, result_type, body

    def accept(self, visitor):
        return visitor.visit_lambda(self)

    def dump(self, indent=0):
        print("  " * indent + f"Lambda {self.name} -> {self.result_type}")
        struct = getattr(self, "struct", None)
        if struct is not None:
            struct.dump(indent + 1)


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


class ChainFieldNode(ExprNode):
    """base.field - the additional task, a '.' after a call whose result
    is a struct: base is itself an expression (a CallNode, a
    MemberCallNode or another ChainFieldNode), never a named variable -
    FieldNode already covers that case. Read via extractvalue in gen.py,
    not gep: base is a temporary value, there is nothing to take the
    address of."""

    def __init__(self, line, col, base, field):
        super().__init__(line, col)
        self.base, self.field = base, field

    def accept(self, visitor):
        return visitor.visit_chain_field(self)

    def dump(self, indent=0):
        print("  " * indent + f"ChainField {self.field}")
        self.base.dump(indent + 1)


class CallNode(ExprNode):
    """name(args): the parser cannot tell a plain function call from a
    callable-object call (Practice 8 Task 1 - name(args) is sugar for
    name.call(args) when name is a variable, not a function), so it always
    builds this node; the semantic pass sets .obj when the rewrite
    applies (gen.py then generates it exactly like a MemberCallNode) -
    .obj is never set at parse time, so --ast (the parse tree) always
    reads "Call", only --ast-checked (after the semantic pass) can read
    "CallableCall"."""

    def __init__(self, line, col, name, args):
        super().__init__(line, col)
        self.name, self.args = name, args

    def accept(self, visitor):
        return visitor.visit_call(self)

    def dump(self, indent=0):
        label = "CallableCall" if getattr(self, "obj", None) is not None else "Call"
        print("  " * indent + f"{label} {self.name}")
        for arg in self.args:
            arg.dump(indent + 1)


class MemberCallNode(ExprNode):
    """obj.name(args): obj is the receiver expression (a VarNode or a
    FieldNode), name is the member function called on it. The call ends
    the chain - nothing can follow its ')'."""

    def __init__(self, line, col, name, obj, args):
        super().__init__(line, col)
        self.name, self.obj, self.args = name, obj, args

    def accept(self, visitor):
        return visitor.visit_member_call(self)

    def dump(self, indent=0):
        print("  " * indent + f"MemberCall {self.name}")
        self.obj.dump(indent + 1)
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
