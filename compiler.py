import sys

from llvmlite import ir
import llvmlite.binding as llvm

from gen import CodeGen
from lexer import CompileError, lex, split_statements
from parser import Parser


def parse(data: bytes):
    tokens = lex(data)
    lines = split_statements(tokens)
    return Parser(lines).parse_program()


def compile_program(program):
    module = ir.Module(name="practice3")
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

    program.accept(CodeGen(builder, printf, fmt_ptr, i32))
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
