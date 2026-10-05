from ast_nodes import (
    AssignNode, BinOpNode, BlockNode, BoolNode, ConstNode, DeclNode, ExitNode,
    IfNode, NotNode, ProgramNode, VarNode, WhileNode,
)
from lexer import CompileError


class Parser:
    """A line cursor on top of the token cursor: most statements are one
    line, but if/else/while are several, so parse_statement may consume
    more than one line of self.lines before returning."""

    def __init__(self, lines):
        self.lines, self.line_pos = lines, 0
        self.toks, self.pos = [], 0

    def peek_line(self):
        return self.lines[self.line_pos] if self.line_pos < len(self.lines) else None

    def next_line(self):
        self.toks, self.pos = self.lines[self.line_pos], 0
        self.line_pos += 1

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

    def end_of_line(self):
        leftover = self.peek()
        if leftover is not None:
            raise self.error(f"unexpected '{leftover.text}' after the statement", leftover)

    def parse_program(self):
        stmts, exit_node = [], None
        while True:
            line = self.peek_line()
            if line is None:
                break
            self.next_line()
            first = self.peek()

            if exit_node is not None:
                raise self.error("no statements are allowed after 'exit'", first)

            if first.kind == "keyword" and first.sub == "statement":
                exit_node = self.parse_exit()
                self.end_of_line()
            elif first.kind == "keyword" and first.sub == "else":
                raise self.error("'else' without an 'if'", first)
            else:
                stmts.append(self.parse_statement(first))

        if exit_node is None:
            raise self.error("program has no 'exit' statement")

        return ProgramNode(1, 1, stmts, exit_node)

    def parse_statement(self, first):
        """first is the already-peeked (not yet eaten) first token of the
        current line; dispatches to the one rule it can start."""
        if first.kind == "keyword" and first.sub == "typename":
            node = self.parse_decl()
            self.end_of_line()
            return node
        if first.kind == "identifier":
            node = self.parse_assign()
            self.end_of_line()
            return node
        if first.kind == "keyword" and first.sub == "if":
            return self.parse_if()
        if first.kind == "keyword" and first.sub == "while":
            return self.parse_while()
        raise self.error(f"cannot start a statement with '{first.text}'", first)

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

    def parse_if(self):
        if_tok = self.eat()
        condition = self.parse_expr()
        self.end_of_line()

        then_block = self.parse_block("if")

        else_block = None
        nxt = self.peek_line()
        if nxt and nxt[0].kind == "keyword" and nxt[0].sub == "else":
            self.next_line()
            self.eat()
            self.end_of_line()
            else_block = self.parse_block("else")

        return IfNode(if_tok.line, if_tok.col, condition, then_block, else_block)

    def parse_while(self):
        while_tok = self.eat()
        condition = self.parse_expr()
        self.end_of_line()

        body = self.parse_block("while")
        return WhileNode(while_tok.line, while_tok.col, condition, body)

    def parse_block(self, after):
        """'{' NL { statement } [ exit NL ] '}' NL - not empty. after names
        the keyword this block follows, for the 'expected {' message."""
        line = self.peek_line()
        if line is None:
            raise self.error(f"expected '{{' on its own line after '{after}', found end of program")
        self.next_line()
        open_tok = self.peek()
        if open_tok is None or not (open_tok.kind == "block" and open_tok.sub == "start"):
            got = open_tok.text if open_tok is not None else "end of line"
            raise self.error(f"expected '{{' on its own line after '{after}', got '{got}'", open_tok)
        self.eat()
        self.end_of_line()

        statements, exit_node = [], None
        while True:
            line = self.peek_line()
            if line is None:
                raise CompileError(open_tok.line, open_tok.col, "'{' is never closed")
            self.next_line()
            first = self.peek()

            if first.kind == "block" and first.sub == "end":
                self.eat()
                self.end_of_line()
                break

            if exit_node is not None:
                raise self.error("statement after 'exit' in the same block", first)

            if first.kind == "keyword" and first.sub == "statement":
                exit_node = self.parse_exit()
                self.end_of_line()
            elif first.kind == "keyword" and first.sub == "else":
                raise self.error("'else' without an 'if'", first)
            else:
                statements.append(self.parse_statement(first))

        if not statements and exit_node is None:
            raise CompileError(open_tok.line, open_tok.col, "empty block")

        return BlockNode(open_tok.line, open_tok.col, statements, exit_node)

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
            raise self.error("expected a constant, 'true'/'false', a variable or '!', "
                              "found end of line")
        if tok.kind == "operator" and tok.text == "!":
            self.eat()
            return NotNode(tok.line, tok.col, self.parse_factor())
        if tok.kind == "constant":
            self.eat()
            return ConstNode(tok.line, tok.col, int(tok.text))
        if tok.kind == "keyword" and tok.sub == "boolean":
            self.eat()
            return BoolNode(tok.line, tok.col, tok.text == "true")
        if tok.kind == "identifier":
            self.eat()
            return VarNode(tok.line, tok.col, tok.text)
        raise self.error(f"expected a constant, 'true'/'false', a variable or '!', "
                          f"got '{tok.text}'", tok)
