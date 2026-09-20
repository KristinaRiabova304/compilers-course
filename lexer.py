"""Task 1: a hand-written lexer state machine over bytes.

No regular expressions, no str.split()/strtok, no lexer generator: a single
loop that reads one byte at a time, looks at the current state and the
byte's class, and either stays in the state, switches to another one, or
emits a token and returns to the start state.
"""


class CompileError(Exception):
    def __init__(self, line, col, msg):
        super().__init__(msg)
        self.line, self.col, self.msg = line, col, msg

    def __str__(self):
        return f"line {self.line}:{self.col}: {self.msg}"


# category, subkind for each reserved word
KEYWORDS = {
    "i32": ("keyword", "typename"),
    "mut": ("keyword", "specifier"),
    "exit": ("keyword", "statement"),
}

SINGLE_BYTE_OPS = {ord("+"): "+", ord("-"): "-", ord("*"): "*"}


class Token:
    __slots__ = ("kind", "sub", "text", "line", "col")

    def __init__(self, kind, sub, text, line, col):
        self.kind, self.sub, self.text, self.line, self.col = kind, sub, text, line, col

    def __repr__(self):
        text = "\\n" if self.text == "\n" else self.text
        pos = f"{self.line}:{self.col}"
        if self.sub:
            return f"({text}, {self.kind}, {self.sub}, {pos})"
        return f"({text}, {self.kind}, {pos})"


def is_ident_start(b):
    return b is not None and (65 <= b <= 90 or 97 <= b <= 122 or b == 95)  # A-Z a-z _


def is_digit(b):
    return b is not None and 48 <= b <= 57  # 0-9


def is_ident_cont(b):
    return is_ident_start(b) or is_digit(b)


def lex(data: bytes):
    """Turn source bytes into a flat list of Token (endline tokens included
    as statement separators, matching the worked example in the practice)."""
    tokens = []
    state = "START"
    start_i = start_line = start_col = 0
    line, col = 1, 1
    open_brace = None  # (line, col) of an unmatched '{' on the current line

    def close_line():
        nonlocal open_brace
        if open_brace is not None:
            l, c = open_brace
            raise CompileError(l, c, "'{' is not closed before the end of the line")
        open_brace = None

    i, n = 0, len(data)
    while i <= n:
        b = data[i] if i < n else None

        if state == "START":
            if b is None:
                break
            elif b in (32, 9):  # space, tab
                pass
            elif b == 10:  # \n
                close_line()
                tokens.append(Token("endline", None, "\n", line, col))
                line += 1
                col = 0
            elif is_ident_start(b):
                state, start_i, start_line, start_col = "IDENT", i, line, col
            elif is_digit(b):
                state, start_i, start_line, start_col = "NUMBER", i, line, col
            elif b == ord("{"):
                if open_brace is not None:
                    raise CompileError(line, col, "unexpected byte '{'")
                open_brace = (line, col)
                tokens.append(Token("block", "start", "{", line, col))
            elif b == ord("}"):
                open_brace = None
                tokens.append(Token("block", "end", "}", line, col))
            elif b in SINGLE_BYTE_OPS:
                tokens.append(Token("operator", None, chr(b), line, col))
            elif b == ord(":"):
                state, start_i, start_line, start_col = "COLON", i, line, col
            elif b == ord("="):
                raise CompileError(line, col, "unexpected byte '='")
            else:
                ch = chr(b) if b < 128 else f"\\x{b:02x}"
                raise CompileError(line, col, f"unexpected byte '{ch}'")

        elif state == "IDENT":
            if is_ident_cont(b):
                pass
            else:
                word = data[start_i:i].decode("ascii")
                kind, sub = KEYWORDS.get(word, ("identifier", None))
                tokens.append(Token(kind, sub, word, start_line, start_col))
                state = "START"
                continue  # re-read this byte in START

        elif state == "NUMBER":
            if is_digit(b):
                pass
            elif is_ident_start(b):
                raise CompileError(start_line, start_col,
                                    f"invalid number: unexpected byte '{chr(b)}' after "
                                    f"'{data[start_i:i].decode('ascii')}'")
            else:
                word = data[start_i:i].decode("ascii")
                tokens.append(Token("constant", "numeric", word, start_line, start_col))
                state = "START"
                continue  # re-read this byte in START

        elif state == "COLON":
            if b == ord("="):
                tokens.append(Token("operator", None, ":=", start_line, start_col))
                state = "START"
            else:
                raise CompileError(start_line, start_col, "':' is not followed by '='")

        i += 1
        col += 1

    close_line()
    return tokens


def split_statements(tokens):
    """Group the flat token stream into per-line statements (blank lines
    are dropped, endline tokens are not part of any statement)."""
    statements, current = [], []
    for tok in tokens:
        if tok.kind == "endline":
            if current:
                statements.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        statements.append(current)
    return statements
