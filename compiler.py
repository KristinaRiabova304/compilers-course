import sys

from llvmlite import ir
import llvmlite.binding as llvm

from lexer import CompileError, lex, split_statements

OPS = {"+", "-", "*"}


def err(tok, msg):
    raise CompileError(tok.line, tok.col, msg)


def parse_operand(tok, symbols):
    """A single constant or variable reference. Reads only the token."""
    if tok.kind == "constant":
        return int(tok.text)
    if tok.kind == "identifier":
        if tok.text not in symbols:
            err(tok, f"variable '{tok.text}' is used before its declaration")
        return tok.text
    err(tok, f"expected a variable or a number, found '{tok.text}'")


def parse_expr(stmt, idx, end, symbols):
    """stmt[idx:end] must be: operand [ op operand ]. Returns a description
    tuple, or None if the range is empty (caller decides whether that's an
    error, since the message differs for a missing initialiser)."""
    if idx >= end:
        return None
    left = parse_operand(stmt[idx], symbols)
    idx += 1
    if idx == end:
        return ("value", left)
    op_tok = stmt[idx]
    if op_tok.kind != "operator" or op_tok.text not in OPS:
        err(op_tok, f"unexpected token '{op_tok.text}'")
    idx += 1
    if idx >= end:
        err(op_tok, f"expected an operand after '{op_tok.text}'")
    right = parse_operand(stmt[idx], symbols)
    idx += 1
    if idx != end:
        err(stmt[idx], f"unexpected token '{stmt[idx].text}'")
    return ("binop", op_tok.text, left, right)


def compile_program(tokens):
    module = ir.Module(name="practice2")
    module.triple = llvm.get_default_triple()
    i32, i8 = ir.IntType(32), ir.IntType(8)

    main_ty = ir.FunctionType(i32, [])
    main_fn = ir.Function(module, main_ty, name="main")
    builder = ir.IRBuilder(main_fn.append_basic_block("entry"))

    printf_ty = ir.FunctionType(i32, [ir.PointerType(i8)], var_arg=True)
    printf = ir.Function(module, printf_ty, name="printf")

    text = b"Program exit with result %d\n\0"
    fmt = ir.GlobalVariable(module, ir.ArrayType(i8, len(text)), name="fmt")
    fmt.linkage, fmt.global_constant = "private", True
    fmt.initializer = ir.Constant(ir.ArrayType(i8, len(text)), bytearray(text))
    fmt_ptr = builder.bitcast(fmt, ir.PointerType(i8))

    symbols = {}  # name -> {"mut": bool, "ptr": alloca}
    has_exit = False

    def emit_operand(val):
        if isinstance(val, int):
            return ir.Constant(i32, val)
        return builder.load(symbols[val]["ptr"])

    def emit_expr(expr):
        if expr[0] == "value":
            return emit_operand(expr[1])
        _, op, left, right = expr
        lv, rv = emit_operand(left), emit_operand(right)
        if op == "+":
            return builder.add(lv, rv)
        if op == "-":
            return builder.sub(lv, rv)
        return builder.mul(lv, rv)

    statements = split_statements(tokens)

    for stmt in statements:
        first = stmt[0]

        if has_exit:
            err(first, "no statements are allowed after 'exit'")

        if first.kind == "keyword" and first.sub == "typename":
            # i32 [mut] NAME { expr }
            idx = 1
            is_mut = False
            if idx < len(stmt) and stmt[idx].kind == "keyword" and stmt[idx].sub == "specifier":
                is_mut = True
                idx += 1
            if idx >= len(stmt) or stmt[idx].kind != "identifier":
                bad = stmt[idx] if idx < len(stmt) else first
                err(bad, "expected a variable name in declaration")
            name_tok = stmt[idx]
            idx += 1
            if name_tok.text in symbols:
                err(name_tok, f"variable '{name_tok.text}' is already declared")

            if idx >= len(stmt) or not (stmt[idx].kind == "block" and stmt[idx].sub == "start"):
                raise CompileError(name_tok.line, name_tok.col + len(name_tok.text),
                                    f"variable '{name_tok.text}' needs an initialiser in {{}}")
            lbrace = stmt[idx]
            idx += 1

            close_idx = idx
            while close_idx < len(stmt) and not (stmt[close_idx].kind == "block"
                                                  and stmt[close_idx].sub == "end"):
                close_idx += 1
            if close_idx >= len(stmt):
                err(lbrace, "'{' is not closed")

            expr = parse_expr(stmt, idx, close_idx, symbols)
            if expr is None:
                err(lbrace, f"variable '{name_tok.text}' needs an initialiser in {{}}")

            rest = close_idx + 1
            if rest != len(stmt):
                err(stmt[rest], f"unexpected token '{stmt[rest].text}'")

            value = emit_expr(expr)
            ptr = builder.alloca(i32, name=name_tok.text)
            builder.store(value, ptr)
            symbols[name_tok.text] = {"mut": is_mut, "ptr": ptr}

        elif (first.kind == "identifier" and len(stmt) >= 2
              and stmt[1].kind == "operator" and stmt[1].text == ":="):
            # NAME := expr
            name_tok = first
            if name_tok.text not in symbols:
                err(name_tok, f"variable '{name_tok.text}' is used before its declaration")
            if not symbols[name_tok.text]["mut"]:
                err(name_tok, f"cannot assign to '{name_tok.text}': it is not mut")

            expr = parse_expr(stmt, 2, len(stmt), symbols)
            if expr is None:
                err(stmt[1], "expected an expression after ':='")

            value = emit_expr(expr)
            builder.store(value, symbols[name_tok.text]["ptr"])

        elif first.kind == "keyword" and first.sub == "statement":
            # exit (NAME | NUMBER)
            if len(stmt) < 2:
                err(first, "expected a value after 'exit'")
            operand = parse_operand(stmt[1], symbols)
            if len(stmt) > 2:
                err(stmt[2], f"unexpected token '{stmt[2].text}'")

            value = emit_operand(operand)
            builder.call(printf, [fmt_ptr, value])
            builder.ret(ir.Constant(i32, 0))
            has_exit = True

        else:
            err(first, f"unexpected token '{first.text}'")

    if not has_exit:
        if tokens:
            last = tokens[-1]
            line, col = last.line, last.col
        else:
            line, col = 1, 1
        raise CompileError(line, col, "program has no 'exit' statement")

    return module


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--tokens":
        with open(sys.argv[2], "rb") as f:
            data = f.read()
        try:
            for tok in lex(data):
                print(tok)
        except CompileError as e:
            sys.stderr.write(f"compilation error: {e}\n")
            sys.exit(1)
        return

    if len(sys.argv) != 3:
        sys.stderr.write("usage: compiler.py input.txt output.ll\n")
        sys.exit(1)

    input_path, output_path = sys.argv[1], sys.argv[2]
    try:
        with open(input_path, "rb") as f:
            data = f.read()
    except OSError as e:
        sys.stderr.write(f"cannot read file: {e}\n")
        sys.exit(1)

    try:
        tokens = lex(data)
        module = compile_program(tokens)
    except CompileError as e:
        sys.stderr.write(f"compilation error: {e}\n")
        sys.exit(1)

    with open(output_path, "w") as f:
        f.write(str(module))


if __name__ == "__main__":
    main()
