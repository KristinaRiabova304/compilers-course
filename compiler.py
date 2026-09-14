import sys
import re
from llvmlite import ir
import llvmlite.binding as llvm

def error(line_no, msg):
    sys.stderr.write(f"line {line_no}: {msg}\n")
    sys.exit(1)

def main():
    if len(sys.argv) != 3:
        sys.exit(1)

    input_path, output_path = sys.argv[1], sys.argv[2]

    try:
        with open(input_path, 'r') as f:
            lines = f.readlines()
    except Exception as e:
        sys.stderr.write(f"Cannot read file: {e}\n")
        sys.exit(1)

    i32, i8 = ir.IntType(32), ir.IntType(8)
    module = ir.Module(name="practice1")
    module.triple = llvm.get_default_triple()

    main_ty = ir.FunctionType(i32, [])
    main_fn = ir.Function(module, main_ty, name="main")
    builder = ir.IRBuilder(main_fn.append_basic_block("entry"))

    printf_ty = ir.FunctionType(i32, [ir.PointerType(i8)], var_arg=True)
    printf = ir.Function(module, printf_ty, name="printf")

    text = b"Program exit with result %d\n\0"
    fmt = ir.GlobalVariable(module, ir.ArrayType(i8, len(text)), name="fmt")
    fmt.linkage, fmt.global_constant = "private", True
    fmt.initializer = ir.Constant(ir.ArrayType(i8, len(text)), bytearray(text))

    symbols = {}
    has_exit = False
    VAR_REGEX = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')

    def is_valid_name(name):
        return bool(VAR_REGEX.match(name)) and name not in {'int', 'exit'}

    def parse_op(op_str, line_no):
        op_str = op_str.strip()
        if op_str.isdigit() or (op_str.startswith('-') and op_str[1:].isdigit()):
            return ir.Constant(i32, int(op_str))
        if not is_valid_name(op_str) or op_str not in symbols:
            error(line_no, f"undeclared or invalid variable '{op_str}'")
        return builder.load(symbols[op_str])

    for idx, line in enumerate(lines, start=1):
        l = line.strip()
        if not l: continue
        if has_exit: error(idx, "code after exit")

        if l.startswith("int "):
            parts = l.split()
            if len(parts) != 2 or not is_valid_name(parts[1]):
                error(idx, "unparsable line")
            var = parts[1]
            if var in symbols:
                error(idx, f"redeclared variable '{var}'")
            symbols[var] = builder.alloca(i32, name=var)

        elif l.startswith("exit "):
            parts = l.split()
            if len(parts) != 2: error(idx, "unparsable line")
            val = parse_op(parts[1], idx)
            fmt_ptr = builder.bitcast(fmt, ir.PointerType(i8))
            builder.call(printf, [fmt_ptr, val])
            builder.ret(ir.Constant(i32, 0))
            has_exit = True

        elif ":=" in l:
            parts = l.split(":=")
            if len(parts) != 2: error(idx, "unparsable line")
            var, rhs = parts[0].strip(), parts[1].strip()
            if not is_valid_name(var) or var not in symbols:
                error(idx, f"undeclared or invalid variable '{var}'")

            op_char = next((op for op in ['+', '-', '*'] if op in rhs), None)
            if op_char:
                sub = rhs.split(op_char)
                if len(sub) != 2: error(idx, "unparsable line")
                left, right = parse_op(sub[0], idx), parse_op(sub[1], idx)
                res = builder.add(left, right) if op_char == '+' else \
                      builder.sub(left, right) if op_char == '-' else \
                      builder.mul(left, right)
            else:
                res = parse_op(rhs, idx)

            builder.store(res, symbols[var])
        else:
            error(idx, "unparsable line")

    if not has_exit:
        error(len(lines) if lines else 1, "no exit print compilation error")

    with open(output_path, "w") as f:
        f.write(str(module))

if __name__ == "__main__":
    main()
