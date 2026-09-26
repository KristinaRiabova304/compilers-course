import sys

from llvmlite import ir
import llvmlite.binding as llvm

from checker import SemanticChecker
from gen import CodeGen
from lexer import CompileError, lex, split_statements
from parser import Parser


def parse(data: bytes):
    tokens = lex(data)
    lines = split_statements(tokens)
    return Parser(lines).parse_program()


def compile_program(program):
    SemanticChecker().check(program)

    module = ir.Module(name="practice4")
    module.triple = llvm.get_default_triple()
    i32, i64, i1, i8 = ir.IntType(32), ir.IntType(64), ir.IntType(1), ir.IntType(8)

    main_ty = ir.FunctionType(i32, [])
    main_fn = ir.Function(module, main_ty, name="main")
    builder = ir.IRBuilder(main_fn.append_basic_block("entry"))

    printf_ty = ir.FunctionType(i32, [ir.PointerType(i8)], var_arg=True)
    printf = ir.Function(module, printf_ty, name="printf")

    def string_const(name, text):
        data = text.encode("ascii") + b"\0"
        g = ir.GlobalVariable(module, ir.ArrayType(i8, len(data)), name=name)
        g.linkage, g.global_constant = "private", True
        g.initializer = ir.Constant(ir.ArrayType(i8, len(data)), bytearray(data))
        return builder.bitcast(g, ir.PointerType(i8))

    fmt_int = string_const("fmt_int", "Program exit with result %lld\n")
    fmt_bool = string_const("fmt_bool", "Program exit with result %s\n")
    str_true = string_const("str_true", "true")
    str_false = string_const("str_false", "false")

    gen = CodeGen(builder, printf, fmt_int, fmt_bool, str_true, str_false, i32, i64, i1)
    program.accept(gen)
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

    if len(sys.argv) == 3 and sys.argv[1] == "--ast":
        with open(sys.argv[2], "rb") as f:
            data = f.read()
        try:
            program = parse(data)
        except CompileError as e:
            sys.stderr.write(f"compilation error: {e}\n")
            sys.exit(1)
        program.dump()
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
        program = parse(data)
        module = compile_program(program)
    except CompileError as e:
        sys.stderr.write(f"compilation error: {e}\n")
        sys.exit(1)

    with open(output_path, "w") as f:
        f.write(str(module))


if __name__ == "__main__":
    main()
