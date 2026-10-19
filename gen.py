"""Task 1/2/3 (Practice 7): code generation for member functions, member
calls, read() and print() on top of the Practice 6 walk (structs,
functions, field access, calls).

The SemanticChecker has already run: every expression node carries
node.type, a VarNode/FieldNode carries node.decl and node.indices (the gep
index chain the checker found by name so this walk never looks a field up
by name - empty for a plain local/param, non-empty for a field access and
also for an implicit self field inside a member function), a
CallNode/MemberCallNode carries node.fn (None for the read() builtin,
checked by name once in the checker, not here). This walk looks nothing
up and raises nothing.

One CodeGen instance is built per function or member function body (and
one for the top-level statements, which live in main): is_top_level picks
whether 'exit' prints and returns 0 (the program ending) or just coerces
its value to the function's declared result type and returns it. A member
function's self.ptrs maps its MemberFnNode to the receiver pointer itself
(compiler.py's extra, leading argument) rather than to an alloca - every
other key (a ParamNode, or a plain fn's own MemberFnNode-less DeclNode)
still maps to an alloca, so gep_chain treats both the same way: one gep
with a leading 0 and then the index chain. Every alloca still goes to the
function's entry block (via builder.goto_entry_block()) so mem2reg can
promote it later. Nested '{}' builds one struct value with chained
insertvalue, bottom up, before a single store.

rt bundles everything shared module-wide (module, i8, printf, scanf, the
format/bool-string globals, a counter for unique string-literal global
names) - print can appear inside any function, not just the top level, so
every CodeGen instance needs it, not just the one for main. Globals are
created once (in compiler.py, or lazily here for a print's own text) but
never pre-bitcast to i8*: a bitcast is an instruction, and an instruction
from one function's entry block cannot be reused as a value in another
function, so every use of a global here goes through as_ptr/literal_ptr,
bitcasting fresh in the current builder each time.
"""
from llvmlite import ir

from ast_nodes import InitNode, StringNode


class CodeGen:
    def __init__(self, builder, ir_types, structs, fn_ir, i32, i64, i1, rt,
                 is_top_level, result_type=None):
        self.builder = builder
        self.ir_types = ir_types    # type name -> ir type, builtins + every struct
        self.structs = structs      # struct name -> StructNode (field_order/field_type)
        self.fn_ir = fn_ir          # FnNode -> ir.Function
        self.i32, self.i64, self.i1 = i32, i64, i1
        self.rt = rt                # shared module-wide runtime, see module docstring
        self.is_top_level = is_top_level
        self.result_type = result_type    # this function's declared result type, if not top level
        self.ptrs = {}  # DeclNode/ParamNode -> alloca ptr
        self.self_ptr = None  # this function's own receiver, only set for a member fn

    def ir_type(self, type_name):
        return self.ir_types[type_name]

    def coerce(self, value, have, want):
        if have == "i32" and want == "i64":
            return self.builder.sext(value, self.i64, name="wide")
        return value

    def gep_chain(self, base_ptr, indices):
        """indices is a chain of struct field indices (the checker found
        them by name); every field lives embedded by value in its
        container, so one gep with a leading 0 (deref the pointer) and
        then one index per link reaches it, nested or not."""
        if not indices:
            return base_ptr
        idx = [ir.Constant(self.i32, 0)] + [ir.Constant(self.i32, i) for i in indices]
        return self.builder.gep(base_ptr, idx, inbounds=True, name="field")

    def as_ptr(self, global_var):
        return self.builder.bitcast(global_var, ir.PointerType(self.rt.i8))

    def literal_ptr(self, text):
        """A fresh global constant for text (a print literal piece or a
        user string), bitcast in the current function - every call site
        gets its own instruction, so this is safe to use from any
        function, unlike reusing one precomputed bitcast everywhere."""
        data = text.encode("latin1") + b"\0"
        name = f"str.{next(self.rt.string_counter)}"
        g = ir.GlobalVariable(self.rt.module, ir.ArrayType(self.rt.i8, len(data)), name=name)
        g.linkage, g.global_constant = "private", True
        g.initializer = ir.Constant(ir.ArrayType(self.rt.i8, len(data)), bytearray(data))
        return self.as_ptr(g)

    def print_literal(self, text):
        self.builder.call(self.rt.printf, [self.as_ptr(self.rt.fmt_print_s), self.literal_ptr(text)])

    def gen_print_value(self, value, type_name):
        """value is never a pointer - a plain scalar, or (for a struct) the
        first-class aggregate itself, walked field by field with
        extract_value. No format carries a trailing newline: visit_print
        adds exactly one, after the whole (possibly nested) value."""
        if type_name == "i32":
            self.builder.call(self.rt.printf, [self.as_ptr(self.rt.fmt_print_i32), value])
        elif type_name == "i64":
            self.builder.call(self.rt.printf, [self.as_ptr(self.rt.fmt_print_i64), value])
        elif type_name == "bool":
            chosen = self.builder.select(value, self.as_ptr(self.rt.str_true),
                                          self.as_ptr(self.rt.str_false), name="print_str")
            self.builder.call(self.rt.printf, [self.as_ptr(self.rt.fmt_print_s), chosen])
        else:
            struct = self.structs[type_name]
            self.print_literal(f"{type_name}{{")
            for idx, field_name in enumerate(struct.field_order):
                self.print_literal(f"{field_name}: " if idx == 0 else f", {field_name}: ")
                field_value = self.builder.extract_value(value, idx)
                self.gen_print_value(field_value, struct.field_type[field_name])
            self.print_literal("}")

    def gen_read(self):
        with self.builder.goto_entry_block():
            ptr = self.builder.alloca(self.i32, name="read_tmp")
        self.builder.call(self.rt.scanf, [self.as_ptr(self.rt.fmt_print_i32), ptr])
        return self.builder.load(ptr)

    def visit_program(self, node):
        for stmt in node.statements:
            stmt.accept(self)
        node.exit.accept(self)

    def visit_block(self, node):
        for stmt in node.statements:
            stmt.accept(self)
        if node.exit:
            node.exit.accept(self)

    def visit_if(self, node):
        cond = node.condition.accept(self)
        function = self.builder.function
        then_bb = function.append_basic_block("then")
        else_bb = function.append_basic_block("else") if node.else_block else None
        merge_bb = function.append_basic_block("merge")
        self.builder.cbranch(cond, then_bb, else_bb or merge_bb)  # ends the current block

        self.builder.position_at_end(then_bb)
        node.then_block.accept(self)
        if not self.builder.block.is_terminated:  # an arm that exited has its ret
            self.builder.branch(merge_bb)

        if node.else_block:
            self.builder.position_at_end(else_bb)
            node.else_block.accept(self)
            if not self.builder.block.is_terminated:
                self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)  # what follows the if goes here

    def visit_while(self, node):
        function = self.builder.function
        cond_bb = function.append_basic_block("while_cond")
        body_bb = function.append_basic_block("while_body")
        end_bb = function.append_basic_block("while_end")

        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cond = node.condition.accept(self)
        self.builder.cbranch(cond, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        node.body.accept(self)
        if not self.builder.block.is_terminated:
            self.builder.branch(cond_bb)  # the back edge

        self.builder.position_at_end(end_bb)

    def gen_init_value(self, init, type_name):
        """Builds the ir.Value an InitNode denotes (never a pointer): for a
        built-in type, its one coerced expression; for a struct, either
        the whole-copy expression as is, or a fresh aggregate built field
        by field with insertvalue, recursing into nested '{}'."""
        if type_name in ("i32", "i64", "bool"):
            item = init.items[0]
            value = item.accept(self)
            return self.coerce(value, item.type, type_name)

        if init.whole_copy:
            return init.items[0].accept(self)

        struct = self.structs[type_name]
        value = ir.Constant(self.ir_type(type_name), ir.Undefined)
        for idx, (item, field_name) in enumerate(zip(init.items, struct.field_order)):
            field_type = struct.field_type[field_name]
            if isinstance(item, InitNode):
                field_value = self.gen_init_value(item, field_type)
            else:
                field_value = self.coerce(item.accept(self), item.type, field_type)
            value = self.builder.insert_value(value, field_value, idx,
                                               name=f"{type_name}.{field_name}")
        return value

    def visit_decl(self, node):
        value = self.gen_init_value(node.init, node.type_name)
        with self.builder.goto_entry_block():
            ptr = self.builder.alloca(self.ir_type(node.type_name), name=node.name)
        self.builder.store(value, ptr)
        self.ptrs[node] = ptr

    def visit_assign(self, node):
        value = node.value.accept(self)
        value = self.coerce(value, node.value.type, node.type)
        ptr = self.gep_chain(self.ptrs[node.decl], node.indices)
        self.builder.store(value, ptr)

    def visit_exit(self, node):
        value = node.value.accept(self)
        if self.is_top_level:
            if node.value.type == "bool":
                chosen = self.builder.select(value, self.as_ptr(self.rt.str_true),
                                              self.as_ptr(self.rt.str_false), name="exit_str")
                self.builder.call(self.rt.printf, [self.as_ptr(self.rt.fmt_bool), chosen])
            else:
                value = self.coerce(value, node.value.type, "i64")
                self.builder.call(self.rt.printf, [self.as_ptr(self.rt.fmt_int), value])
            self.builder.ret(ir.Constant(self.i32, 0))
        else:
            value = self.coerce(value, node.value.type, self.result_type)
            self.builder.ret(value)

    def visit_binop(self, node):
        lv, rv = node.left.accept(self), node.right.accept(self)
        lt, rt = node.left.type, node.right.type

        if node.op in ("+", "-", "*"):
            lv, rv = self.coerce(lv, lt, node.type), self.coerce(rv, rt, node.type)
            if node.op == "+":
                return self.builder.add(lv, rv)
            if node.op == "-":
                return self.builder.sub(lv, rv)
            return self.builder.mul(lv, rv)

        if lt != "bool":
            width = "i64" if "i64" in (lt, rt) else "i32"
            lv, rv = self.coerce(lv, lt, width), self.coerce(rv, rt, width)
        predicate = "==" if node.op == "==" else "!="
        return self.builder.icmp_signed(predicate, lv, rv)

    def visit_not(self, node):
        return self.builder.not_(node.operand.accept(self), name="not")

    def visit_var(self, node):
        ptr = self.gep_chain(self.ptrs[node.decl], node.indices)
        return self.builder.load(ptr)

    def visit_field(self, node):
        ptr = self.gep_chain(self.ptrs[node.decl], node.indices)
        return self.builder.load(ptr)

    def visit_call(self, node):
        if node.fn is None:  # the read() builtin - checker found no FnNode for it
            return self.gen_read()
        args = [self.self_ptr] if node.on_self else []
        for arg, param_decl in zip(node.args, node.fn.param_decls):
            value = arg.accept(self)
            args.append(self.coerce(value, arg.type, param_decl.type_name))
        return self.builder.call(self.fn_ir[node.fn], args, name="call")

    def visit_member_call(self, node):
        if hasattr(node.obj, "decl"):
            self_ptr = self.gep_chain(self.ptrs[node.obj.decl], node.obj.indices)
        else:
            # the additional task: obj is a temporary (a call, a member
            # call or a chain field) - store it into a fresh alloca and
            # pass that address as this, the same as spelling it out by
            # hand would: "for a member function call the result is
            # stored into a temporary alloca, and its address is passed"
            value = node.obj.accept(self)
            with self.builder.goto_entry_block():
                self_ptr = self.builder.alloca(self.ir_type(node.obj.type), name="tmp")
            self.builder.store(value, self_ptr)
        args = [self_ptr]
        for arg, param_decl in zip(node.args, node.fn.param_decls):
            value = arg.accept(self)
            args.append(self.coerce(value, arg.type, param_decl.type_name))
        return self.builder.call(self.fn_ir[node.fn], args, name="call")

    def visit_chain_field(self, node):
        value = node.base.accept(self)
        return self.builder.extract_value(value, node.index, name="field")

    def visit_print(self, node):
        if isinstance(node.arg, StringNode):
            self.print_literal(node.arg.value)
        else:
            value = node.arg.accept(self)
            self.gen_print_value(value, node.arg.type)
        self.print_literal("\n")

    def visit_const(self, node):
        return ir.Constant(self.ir_type(node.type), node.value)

    def visit_bool(self, node):
        return ir.Constant(self.i1, 1 if node.value else 0)
