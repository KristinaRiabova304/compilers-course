import itertools
import sys
import types

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
    checker = SemanticChecker()
    checker.check(program)

    module = ir.Module(name="practice7")
    module.triple = llvm.get_default_triple()
    i32, i64, i1, i8 = ir.IntType(32), ir.IntType(64), ir.IntType(1), ir.IntType(8)

    ir_types = {"i32": i32, "i64": i64, "bool": i1}
    for struct in program.structs:  # declared-above order, so every field type is already in here
        struct_ty = ir.global_context.get_identified_type(struct.name)
        struct_ty.set_body(*(ir_types[field_type] for field_type in
                              (struct.field_type[name] for name in struct.field_order)))
        ir_types[struct.name] = struct_ty

    # first collect every function's signature as an ir.Function, so a call
    # to a function declared below (or to itself) finds it already made
    fn_ir = {}
    for fn in program.fns:
        param_types = [ir_types[p.type_name] for p in fn.param_decls]
        fn_ty = ir.FunctionType(ir_types[fn.result_type], param_types)
        fn_ir[fn] = ir.Function(module, fn_ty, name=f"fn_{fn.name}")

    # a member fn takes one extra leading argument, a pointer to its
    # receiver (so a write through a bare field name is visible to the
    # caller) - everything else about its signature is the same
    for struct in program.structs:
        for member_fn in struct.fns:
            param_types = [ir.PointerType(ir_types[struct.name])] + \
                [ir_types[p.type_name] for p in member_fn.param_decls]
            fn_ty = ir.FunctionType(ir_types[member_fn.result_type], param_types)
            fn_ir[member_fn] = ir.Function(module, fn_ty, name=f"{struct.name}_{member_fn.name}")
            fn_ir[member_fn].args[0].name = "this"

    main_ty = ir.FunctionType(i32, [])
    main_fn = ir.Function(module, main_ty, name="main")
    builder = ir.IRBuilder(main_fn.append_basic_block("entry"))

    i8p = ir.PointerType(i8)
    printf = ir.Function(module, ir.FunctionType(i32, [i8p], var_arg=True), name="printf")
    scanf = ir.Function(module, ir.FunctionType(i32, [i8p], var_arg=True), name="scanf")

    def global_string(name, text):
        """A raw global constant, not yet bitcast to i8* - exit and print
        both read globals through CodeGen.as_ptr, bitcasting fresh in
        whichever function's builder is current, since a bitcast is an
        instruction and one function's instructions cannot be reused as a
        value in another."""
        data = text.encode("latin1") + b"\0"
        g = ir.GlobalVariable(module, ir.ArrayType(i8, len(data)), name=name)
        g.linkage, g.global_constant = "private", True
        g.initializer = ir.Constant(ir.ArrayType(i8, len(data)), bytearray(data))
        return g

    # shared module-wide context every CodeGen instance needs - print (and
    # so printf/scanf/the format globals) can appear inside any function,
    # not just the one that runs at the top level
    rt = types.SimpleNamespace(
        module=module, i8=i8, printf=printf, scanf=scanf,
        fmt_int=global_string("fmt_int", "Program exit with result %lld\n"),
        fmt_bool=global_string("fmt_bool", "Program exit with result %s\n"),
        str_true=global_string("str_true", "true"),
        str_false=global_string("str_false", "false"),
        fmt_print_i32=global_string("fmt_print_i32", "%d"),
        fmt_print_i64=global_string("fmt_print_i64", "%lld"),
        fmt_print_s=global_string("fmt_print_s", "%s"),
        string_counter=itertools.count(),
    )

    gen = CodeGen(builder, ir_types, checker.structs, fn_ir, i32, i64, i1, rt, is_top_level=True)
    program.accept(gen)

    for fn in program.fns:
        fn_builder = ir.IRBuilder(fn_ir[fn].append_basic_block("entry"))
        fn_gen = CodeGen(fn_builder, ir_types, checker.structs, fn_ir, i32, i64, i1, rt,
                          is_top_level=False, result_type=fn.result_type)
        for param_decl, arg in zip(fn.param_decls, fn_ir[fn].args):
            ptr = fn_builder.alloca(ir_types[param_decl.type_name], name=param_decl.name)
            fn_builder.store(arg, ptr)
            fn_gen.ptrs[param_decl] = ptr
        fn.body.accept(fn_gen)

    for struct in program.structs:
        for member_fn in struct.fns:
            fn_builder = ir.IRBuilder(fn_ir[member_fn].append_basic_block("entry"))
            fn_gen = CodeGen(fn_builder, ir_types, checker.structs, fn_ir, i32, i64, i1, rt,
                              is_top_level=False, result_type=member_fn.result_type)
            self_ptr, *param_args = fn_ir[member_fn].args
            fn_gen.ptrs[member_fn] = self_ptr  # already a pointer: no alloca needed
            fn_gen.self_ptr = self_ptr  # for a bare call on a sibling member fn
            for param_decl, arg in zip(member_fn.param_decls, param_args):
                ptr = fn_builder.alloca(ir_types[param_decl.type_name], name=param_decl.name)
                fn_builder.store(arg, ptr)
                fn_gen.ptrs[param_decl] = ptr
            member_fn.body.accept(fn_gen)

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
