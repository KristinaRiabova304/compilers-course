from ast_nodes import (
    AssignNode, BinOpNode, BlockNode, BoolNode, CallNode, ConstNode, DeclNode,
    ExitNode, FieldNode, FnNode, IfNode, InitNode, NotNode, ProgramNode,
    StructNode, VarNode, WhileNode,
)
from lexer import CompileError


class Parser:
    """A line cursor on top of the token cursor: most statements are one
    line, but if/else/while/struct/fn are several, so parse_statement may
    consume more than one line of self.lines before returning."""

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

    def peek_next(self):
        return self.toks[self.pos + 1] if self.pos + 1 < len(self.toks) else None

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

    def is_type_token(self, tok):
        """A type is a built-in typename or a struct name (any identifier -
        the checker tells a real struct name from an unknown one)."""
        return tok is not None and ((tok.kind == "keyword" and tok.sub == "typename")
                                     or tok.kind == "identifier")

    def parse_program(self):
        structs, fns, stmts, exit_node = [], [], [], None
        phase = "structs"  # structs -> fns -> statements, never back

        while True:
            line = self.peek_line()
            if line is None:
                break
            self.next_line()
            first = self.peek()

            if exit_node is not None:
                raise self.error("no statements are allowed after 'exit'", first)

            if first.kind == "keyword" and first.sub == "struct":
                if phase != "structs":
                    raise self.error("a struct must be declared before "
                                      "all functions and statements", first)
                structs.append(self.parse_struct())
                continue

            if first.kind == "keyword" and first.sub == "fn":
                if phase == "statements":
                    raise self.error("a function must be declared before all statements", first)
                phase = "fns"
                fns.append(self.parse_fn())
                continue

            phase = "statements"
            if first.kind == "keyword" and first.sub == "statement":
                exit_node = self.parse_exit()
                self.end_of_line()
            elif first.kind == "keyword" and first.sub == "else":
                raise self.error("'else' without an 'if'", first)
            else:
                stmts.append(self.parse_statement(first))

        if exit_node is None:
            raise self.error("program has no 'exit' statement")

        return ProgramNode(1, 1, structs, fns, stmts, exit_node)

    def parse_struct(self):
        struct_tok = self.eat()  # 'struct'
        name_tok = self.peek()
        if name_tok is None or name_tok.kind != "identifier":
            raise self.error("expected a struct name", name_tok)
        self.eat()
        self.end_of_line()

        line = self.peek_line()
        if line is None:
            raise self.error(f"expected '{{' on its own line after "
                              f"'struct {name_tok.text}', found end of program")
        self.next_line()
        open_tok = self.peek()
        if open_tok is None or not (open_tok.kind == "block" and open_tok.sub == "start"):
            got = open_tok.text if open_tok is not None else "end of line"
            raise self.error(f"expected '{{' on its own line after "
                              f"'struct {name_tok.text}', got '{got}'", open_tok)
        self.eat()
        self.end_of_line()

        fields = []
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
            fields.append(self.parse_field_decl())

        if not fields:
            raise CompileError(struct_tok.line, struct_tok.col,
                                f"struct '{name_tok.text}' has no fields")

        return StructNode(struct_tok.line, struct_tok.col, name_tok.text, fields)

    def parse_field_decl(self):
        type_tok = self.peek()
        if not self.is_type_token(type_tok):
            got = type_tok.text if type_tok is not None else "end of line"
            raise self.error(f"expected a field type, got '{got}'", type_tok)
        self.eat()
        type_name = type_tok.text

        mutable = False
        tok = self.peek()
        if tok is not None and tok.kind == "keyword" and tok.sub == "specifier":
            mutable = True
            self.eat()

        name_tok = self.peek()
        if name_tok is None or name_tok.kind != "identifier":
            got = name_tok.text if name_tok is not None else "end of line"
            raise self.error(f"expected a field name, got '{got}'", name_tok)
        self.eat()
        self.end_of_line()
        return (type_name, mutable, name_tok.text, type_tok.line, type_tok.col)

    def parse_fn(self):
        fn_tok = self.eat()  # 'fn'
        name_tok = self.peek()
        if name_tok is None or name_tok.kind != "identifier":
            raise self.error("expected a function name", name_tok)
        self.eat()

        op = self.peek()
        if op is None or not (op.kind == "operator" and op.text == ":="):
            got = op.text if op is not None else "end of line"
            raise self.error(f"expected ':=' after '{name_tok.text}', got '{got}'", op)
        self.eat()

        open_paren = self.peek()
        if open_paren is None or not (open_paren.kind == "paren" and open_paren.sub == "start"):
            raise self.error("expected '(' to start the parameter list", open_paren)
        self.eat()

        params = []
        tok = self.peek()
        if tok is not None and not (tok.kind == "paren" and tok.sub == "end"):
            params.append(self.parse_param())
            while True:
                tok = self.peek()
                if tok is not None and tok.kind == "operator" and tok.text == ",":
                    self.eat()
                    params.append(self.parse_param())
                else:
                    break

        close_paren = self.peek()
        if close_paren is None or not (close_paren.kind == "paren" and close_paren.sub == "end"):
            raise self.error("expected ')' to close the parameter list", close_paren)
        self.eat()

        arrow = self.peek()
        if arrow is None or not (arrow.kind == "operator" and arrow.text == "->"):
            raise self.error("expected '->' and a result type", arrow)
        self.eat()

        result_tok = self.peek()
        if not self.is_type_token(result_tok):
            got = result_tok.text if result_tok is not None else "end of line"
            raise self.error(f"expected a result type, got '{got}'", result_tok)
        self.eat()
        result_type = result_tok.text
        self.end_of_line()

        body = self.parse_block(f"fn {name_tok.text}")
        return FnNode(fn_tok.line, fn_tok.col, name_tok.text, params, result_type, body)

    def parse_param(self):
        type_tok = self.peek()
        if not self.is_type_token(type_tok):
            got = type_tok.text if type_tok is not None else "end of line"
            raise self.error(f"expected a parameter type, got '{got}'", type_tok)
        self.eat()
        name_tok = self.peek()
        if name_tok is None or name_tok.kind != "identifier":
            got = name_tok.text if name_tok is not None else "end of line"
            raise self.error(f"expected a parameter name, got '{got}'", name_tok)
        self.eat()
        return (type_tok.text, name_tok.text, type_tok.line, type_tok.col)

    def parse_statement(self, first):
        """first is the already-peeked (not yet eaten) first token of the
        current line; dispatches to the one rule it can start."""
        if first.kind == "keyword" and first.sub == "typename":
            node = self.parse_decl()
            self.end_of_line()
            return node
        if first.kind == "identifier":
            second = self.peek_next()
            if second is not None and second.kind == "paren" and second.sub == "start":
                raise self.error("a call cannot stand alone as a statement", first)
            if second is not None and (second.kind == "identifier"
                                        or (second.kind == "keyword"
                                            and second.sub == "specifier")):
                node = self.parse_decl()
            else:
                node = self.parse_assign()
            self.end_of_line()
            return node
        if first.kind == "keyword" and first.sub == "if":
            return self.parse_if()
        if first.kind == "keyword" and first.sub == "while":
            return self.parse_while()
        raise self.error(f"cannot start a statement with '{first.text}'", first)

    def parse_dot_fields(self):
        """Consumes zero or more '.' ident, returns the list of field names."""
        fields = []
        while True:
            tok = self.peek()
            if tok is not None and tok.kind == "operator" and tok.text == ".":
                self.eat()
                field_tok = self.peek()
                if field_tok is None or field_tok.kind != "identifier":
                    got = field_tok.text if field_tok is not None else "end of line"
                    raise self.error(f"expected a field name after '.', got '{got}'", field_tok)
                self.eat()
                fields.append(field_tok.text)
            else:
                return fields

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

        init = self.parse_init()

        return DeclNode(name_tok.line, name_tok.col, type_name, name_tok.text, mutable, init)

    def parse_init(self):
        """'{' item { ',' item } '}' where item is an expr or a nested '{...}'."""
        open_tok = self.eat()  # '{'
        items = [self.parse_init_item()]
        while True:
            tok = self.peek()
            if tok is not None and tok.kind == "operator" and tok.text == ",":
                self.eat()
                items.append(self.parse_init_item())
            else:
                break
        close = self.peek()
        if close is None or not (close.kind == "block" and close.sub == "end"):
            raise self.error("expected '}' to close the initialiser", close)
        self.eat()
        return InitNode(open_tok.line, open_tok.col, items)

    def parse_init_item(self):
        tok = self.peek()
        if tok is not None and tok.kind == "block" and tok.sub == "start":
            return self.parse_init()
        return self.parse_expr()

    def parse_assign(self):
        name_tok = self.eat()
        fields = self.parse_dot_fields()

        op = self.peek()
        chain = ".".join([name_tok.text, *fields])
        if op is None:
            raise self.error(f"expected ':=' after '{chain}', found end of line")
        if not (op.kind == "operator" and op.text == ":="):
            raise self.error(f"expected ':=' after '{chain}', got '{op.text}'", op)
        self.eat()

        value = self.parse_expr()
        return AssignNode(name_tok.line, name_tok.col, name_tok.text, fields, value)

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
            raise self.error("expected a constant, 'true'/'false', a variable, a field, "
                              "a call or '!', found end of line")
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
            nxt = self.peek()
            if nxt is not None and nxt.kind == "paren" and nxt.sub == "start":
                return self.parse_call(tok)
            fields = self.parse_dot_fields()
            if fields:
                return FieldNode(tok.line, tok.col, tok.text, fields)
            return VarNode(tok.line, tok.col, tok.text)
        raise self.error(f"expected a constant, 'true'/'false', a variable, a field, "
                          f"a call or '!', got '{tok.text}'", tok)

    def parse_call(self, name_tok):
        self.eat()  # '('
        args = []
        tok = self.peek()
        if tok is not None and not (tok.kind == "paren" and tok.sub == "end"):
            args.append(self.parse_expr())
            while True:
                tok = self.peek()
                if tok is not None and tok.kind == "operator" and tok.text == ",":
                    self.eat()
                    args.append(self.parse_expr())
                else:
                    break
        close = self.peek()
        if close is None or not (close.kind == "paren" and close.sub == "end"):
            raise self.error("expected ')' to close the call", close)
        self.eat()
        return CallNode(name_tok.line, name_tok.col, name_tok.text, args)
