from ast_nodes import AssignNode, BinOpNode, BoolNode, ConstNode, DeclNode, ExitNode, ProgramNode, VarNode
from lexer import CompileError


class Parser:
    def __init__(self, lines):
        self.lines, self.toks, self.pos = lines, [], 0

    def peek(self):
        return self.toks[self.pos] if self.pos < len(self.toks) else None

    def eat(self):
        tok = self.toks[self.pos]
        self.pos += 1
        return tok

    def error(self, msg, tok=None):
        if tok is not None:
            return CompileError(tok.line, tok.col, msg)
        if self.toks:
            last = self.toks[-1]
            return CompileError(last.line, last.col + len(last.text), msg)
        return CompileError(1, 1, msg)

    def parse_program(self):
        stmts, exit_node = [], None
        for toks in self.lines:
            self.toks, self.pos = toks, 0
            first = self.peek()
            if first is None:
                continue

            if exit_node is not None:
                raise self.error("no statements are allowed after 'exit'", first)

            if first.kind == "keyword" and first.sub == "statement":
                exit_node = self.parse_exit()
            elif first.kind == "keyword" and first.sub == "typename":
                stmts.append(self.parse_decl())
            elif first.kind == "identifier":
                stmts.append(self.parse_assign())
            else:
                raise self.error(f"cannot start a statement with '{first.text}'", first)

            leftover = self.peek()
            if leftover is not None:
                raise self.error(f"unexpected '{leftover.text}' after the statement", leftover)

        if exit_node is None:
            if self.lines:
                self.toks = self.lines[-1]
            raise self.error("program has no 'exit' statement")

        return ProgramNode(1, 1, stmts, exit_node)

    def parse_decl(self):
        type_tok = self.eat()
        type_name = type_tok.text

        mutable = False
        tok = self.peek()
        if tok is not None and tok.kind == "keyword" and tok.sub == "specifier":
            mutable = True
            self.eat()

        name_tok = self.peek()
        if name_tok is None:
            raise self.error("expected a variable name, found end of line")
        if name_tok.kind != "identifier":
            raise self.error(f"expected a variable name, got '{name_tok.text}'", name_tok)
        self.eat()

        brace = self.peek()
        if brace is None:
            raise CompileError(name_tok.line, name_tok.col + len(name_tok.text),
                                f"variable '{name_tok.text}' needs an initialiser in {{}}")
        if not (brace.kind == "block" and brace.sub == "start"):
            raise self.error(f"variable '{name_tok.text}' needs an initialiser in {{}}", brace)
        self.eat()

        init = self.parse_expr()

        close = self.peek()
        if close is None or not (close.kind == "block" and close.sub == "end"):
            raise self.error("expected '}' to close the initialiser", close)
        self.eat()

        return DeclNode(name_tok.line, name_tok.col, type_name, name_tok.text, mutable, init)

    def parse_assign(self):
        name_tok = self.eat()
        op = self.peek()
        if op is None:
            raise self.error(f"expected ':=' after '{name_tok.text}', found end of line")
        if not (op.kind == "operator" and op.text == ":="):
            raise self.error(f"expected ':=' after '{name_tok.text}', got '{op.text}'", op)
        self.eat()

        value = self.parse_expr()
        return AssignNode(name_tok.line, name_tok.col, name_tok.text, value)

    def parse_exit(self):
        exit_tok = self.eat()
        value = self.parse_factor()
        return ExitNode(exit_tok.line, exit_tok.col, value)

    def parse_expr(self):
        node = self.parse_arith()
        tok = self.peek()
        if tok is not None and tok.kind == "operator" and tok.text in ("==", "!="):
            self.eat()
            node = BinOpNode(tok.line, tok.col, tok.text, node, self.parse_arith())
        return node

    def parse_arith(self):
        node = self.parse_term()
        while True:
            tok = self.peek()
            if tok is not None and tok.kind == "operator" and tok.text in ("+", "-"):
                self.eat()
                node = BinOpNode(tok.line, tok.col, tok.text, node, self.parse_term())
            else:
                return node

    def parse_term(self):
        node = self.parse_factor()
        while True:
            tok = self.peek()
            if tok is not None and tok.kind == "operator" and tok.text == "*":
                self.eat()
                node = BinOpNode(tok.line, tok.col, tok.text, node, self.parse_factor())
            else:
                return node

    def parse_factor(self):
        tok = self.peek()
        if tok is None:
            raise self.error("expected a constant, 'true'/'false' or a variable, found end of line")
        if tok.kind == "constant":
            self.eat()
            return ConstNode(tok.line, tok.col, int(tok.text))
        if tok.kind == "keyword" and tok.sub == "boolean":
            self.eat()
            return BoolNode(tok.line, tok.col, tok.text == "true")
        if tok.kind == "identifier":
            self.eat()
            return VarNode(tok.line, tok.col, tok.text)
        raise self.error(f"expected a constant, 'true'/'false' or a variable, got '{tok.text}'", tok)
