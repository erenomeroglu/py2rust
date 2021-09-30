import ast
from py2many.exceptions import AstIncompatibleAssign
from .inference import (
    NUM_TYPES,
    INTEGER_TYPES
)
import textwrap

from .clike import CLikeTranspiler
from .plugins import (
    ATTR_DISPATCH_TABLE,
    CLASS_DISPATCH_TABLE,
    FUNC_DISPATCH_TABLE,
    MODULE_DISPATCH_TABLE,
    DISPATCH_MAP,
    SMALL_DISPATCH_MAP,
    SMALL_USINGS_MAP,
    JuliaTranspilerPlugins,
)

from py2many.analysis import get_id, is_void_function
from py2many.declaration_extractor import DeclarationExtractor
from py2many.clike import _AUTO_INVOKED, class_for_typename
from py2many.tracer import is_list, defined_before, is_class_or_module, is_enum

from typing import Collection, List, Tuple


class JuliaMethodCallRewriter(ast.NodeTransformer):
    def visit_Call(self, node):
        fname = node.func
        if isinstance(fname, ast.Attribute):
            if is_list(node.func.value) and fname.attr == "append":
                new_func_name = "push!"
            else:
                new_func_name = fname.attr
            if get_id(fname.value):
                node0 = ast.Name(id=get_id(fname.value), lineno=node.lineno)
            else:
                node0 = fname.value

            node.args = [node0] + node.args
            node.func = ast.Name(id=new_func_name, lineno=node.lineno, ctx=fname.ctx)
        return node


class JuliaTranspiler(CLikeTranspiler):
    NAME = "julia"

    CONTAINER_TYPE_MAP = {
        "List": "Array",
        "Dict": "Dict",
        "Set": "Set",
        "Optional": "Nothing",
    }

    def __init__(self):
        super().__init__()
        self._headers = set([])
        self._default_type = ""
        self._container_type_map = self.CONTAINER_TYPE_MAP
        self._dispatch_map = DISPATCH_MAP
        self._small_dispatch_map = SMALL_DISPATCH_MAP
        self._small_usings_map = SMALL_USINGS_MAP
        self._func_dispatch_table = FUNC_DISPATCH_TABLE
        self._attr_dispatch_table = ATTR_DISPATCH_TABLE

    def usings(self):
        usings = sorted(list(set(self._usings)))
        uses = "\n".join(f"using {mod}" for mod in usings)
        return uses

    def comment(self, text):
        return f"#= {text} \n=#"

    def _combine_value_index(self, value_type, index_type) -> str:
        return f"{value_type}{{{index_type}}}"

    def visit_Constant(self, node) -> str:
        if node.value is True:
            return "true"
        elif node.value is False:
            return "false"
        elif node.value is None:
            return "nothing"
        elif isinstance(node.value, complex):
            str_value = str(node.value)
            return (
                str_value.replace("j", "im") if str_value.endswith("j") else str_value
            )
        else:
            return super().visit_Constant(node)

    def visit_FunctionDef(self, node) -> str:
        body = "\n".join([self.visit(n) for n in node.body])
        typenames, args = self.visit(node.args)

        args_list = []
        typedecls = []
        index = 0

        is_python_main = getattr(node, "python_main", False)

        if len(typenames) and typenames[0] == None and hasattr(node, "self_type"):
            typenames[0] = node.self_type

        for i in range(len(args)):
            typename = typenames[i]
            arg = args[i]
            # Resolve alias imports
            resolved_import = super().visit_alias_import_typename(typename)
            if(resolved_import != None):
                typename = resolved_import
            elif typename == "T": 
                # Allow the user to know that type is generic
                typename = "T{0}".format(index)
                typedecls.append(typename)
                index += 1

            args_list.append("{0}::{1}".format(arg, typename))

        return_type = ""
        if not is_void_function(node):
            if node.returns:
                typename = self._typename_from_annotation(node, attr="returns")
                return_type = f"::{typename}"
            else:
                # Allow Julia to infer types
                return_type = ""

        template = ""
        if len(typedecls) > 0:
            template = "{{{0}}}".format(", ".join(typedecls))

        args = ", ".join(args_list)
        funcdef = f"function {node.name}{template}({args}){return_type}"
        maybe_main = ""
        if is_python_main:
            maybe_main = "\nmain()"
        return f"{funcdef}\n{body}\nend\n{maybe_main}"

    def visit_Return(self, node) -> str:
        if node.value:
            return "return {0}".format(self.visit(node.value))
        return "return"

    def visit_arg(self, node):
        id = get_id(node)
        if id == "self":
            return (None, "self")
        typename = "T"
        if node.annotation:
            typename = self._typename_from_annotation(node)
        return (typename, id)

    def visit_Lambda(self, node) -> str:
        _, args = self.visit(node.args)
        args_string = ", ".join(args)
        body = self.visit(node.body)
        return "({0}) -> {1}".format(args_string, body)

    def visit_Attribute(self, node) -> str:
        attr = node.attr

        value_id = self.visit(node.value)

        if not value_id:
            value_id = ""

        if value_id == "sys":
            if attr == "argv":
                return "append!([PROGRAM_FILE], ARGS)"

        if is_enum(value_id, node.scopes):
            return f"{value_id}.{attr}"

        if is_class_or_module(value_id, node.scopes):
            return f"{value_id}::{attr}"

        return f"{value_id}.{attr}"

    def visit_range(self, node, vargs: List[str]) -> str:
        if len(node.args) == 1:
            return f"(0:{vargs[0]} - 1)"
        elif (len(node.args) == 2) or ((len(node.args) == 3) and (vargs[2] == "1")):
            return f"({vargs[0]}:{vargs[1]} - 1)"
        elif len(node.args) == 3:
            return f"({vargs[0]}:{vargs[2]}:{vargs[1]}-1)"

        raise Exception(
            "encountered range() call with unknown parameters: range({})".format(vargs)
        )

    def _visit_print(self, node, vargs: List[str]) -> str:
        args = ", ".join(vargs)
        return f'println({args})'

    def visit_Call(self, node) -> str:
        fname = self.visit(node.func)
        fndef = node.scopes.find(fname)
        vargs = []

        if node.args:
            vargs += [self.visit(a) for a in node.args]
        if node.keywords:
            vargs += [self.visit(kw.value) for kw in node.keywords]

        ret = self._dispatch(node, fname, vargs)
        if ret is not None:
            return ret

        if fndef and hasattr(fndef, "args"):
            converted = []
            for varg, fnarg, node_arg in zip(vargs, fndef.args.args, node.args):
                actual_type = self._typename_from_annotation(node_arg)
                declared_type = self._typename_from_annotation(fnarg)
                if declared_type != None and declared_type != "" and actual_type != declared_type and actual_type != self._default_type:
                    converted.append(f"convert({declared_type}, {varg})")
                else:
                    converted.append(varg)
        else:
            converted = vargs

        if fname == "join":
            converted.reverse()
        args = ", ".join(converted)
        return f"{fname}({args})"

    def visit_For(self, node) -> str:
        target = self.visit(node.target)
        it = self.visit(node.iter)
        buf = []
        buf.append("for {0} in {1}".format(target, it))
        buf.extend([self.visit(c) for c in node.body])
        buf.append("end")
        return "\n".join(buf)

    def visit_Str(self, node) -> str:
        return "" + super().visit_Str(node) + ""

    def visit_Bytes(self, node) -> str:
        bytes_str = node.s
        bytes_str = bytes_str.replace(b'"', b'\\"')
        return 'b"' + bytes_str.decode("ascii", "backslashreplace") + '"'

    def visit_Compare(self, node) -> str:
        left = self.visit(node.left)
        right = self.visit(node.comparators[0])

        if hasattr(node.comparators[0], "annotation"):
            self._generic_typename_from_annotation(node.comparators[0])
            value_type = getattr(
                node.comparators[0].annotation, "generic_container_type", None
            )
            if value_type and value_type[0] == "Dict":
                right = f"keys({right})"

        if isinstance(node.ops[0], ast.In):
            return "{0} in {1}".format(left, right) #  not recognized: \u2208
        elif isinstance(node.ops[0], ast.NotIn):
            return "{0} not in {1}".format(left, right) # ∉ not recognized: \u2209

        return super().visit_Compare(node)

    def visit_Name(self, node) -> str:
        if get_id(node) == "None":
            return "Nothing"
        else:
            return super().visit_Name(node)

    def visit_NameConstant(self, node) -> str:
        if node.value is True:
            return "true"
        elif node.value is False:
            return "false"
        elif node.value is None:
            return "Nothing"
        else:
            return super().visit_NameConstant(node)

    def visit_If(self, node) -> str:
        body_vars = set([get_id(v) for v in node.scopes[-1].body_vars])
        orelse_vars = set([get_id(v) for v in node.scopes[-1].orelse_vars])
        node.common_vars = body_vars.intersection(orelse_vars)

        buf = []
        cond = self.visit(node.test)
        buf.append(f"if {cond}")
        buf.extend([self.visit(child) for child in node.body])

        orelse = [self.visit(child) for child in node.orelse]
        if orelse:
            buf.append("else\n")
            buf.extend(orelse)
            buf.append("end")
        else:
            buf.append("end")

        return "\n".join(buf)

    def visit_While(self, node) -> str:
        buf = []
        buf.append("while {0}".format(self.visit(node.test)))
        buf.extend([self.visit(n) for n in node.body])
        buf.append("end")
        return "\n".join(buf)

    def visit_UnaryOp(self, node) -> str:
        if isinstance(node.op, ast.USub):
            if isinstance(node.operand, (ast.Call, ast.Num)):
                # Shortcut if parenthesis are not needed
                return "-{0}".format(self.visit(node.operand))
            else:
                return "-({0})".format(self.visit(node.operand))
        else:
            return super().visit_UnaryOp(node)

    def visit_BinOp(self, node) -> str:
        # print("Num Left " + str(isinstance(node.right, ast.Num)))
        # print("Num Right " + str(isinstance(node.left, ast.Num)))
        # print("List Left " + str(isinstance(node.right, ast.Name)))
        # print("List Right " + str(isinstance(node.left, ast.Name)))

        if isinstance(node.op, ast.Mult):
            if((isinstance(node.right, ast.Num) and isinstance(node.left, ast.Num)) or
                (isinstance(node.right, ast.Num) and node.left.julia_annotation in NUM_TYPES) or
                (isinstance(node.left, ast.Num) and node.right.julia_annotation in NUM_TYPES)):
                return "{0}*{1}".format(
                    self.visit(node.left), self.visit(node.right)
                )
            elif(isinstance(node.right, ast.Num) and (isinstance(node.left, ast.List) or node.left.julia_annotation == "List")):
                print(node.scopes[-1].name)
                left = self.visit_List(node.left) if isinstance(node.left, ast.List) else self.visit(node.left)
                return "repeat({0},{1})".format(
                    left, self.visit(node.right)
                )
            elif(isinstance(node.left, ast.Num) and (isinstance(node.right, ast.List) or node.right.julia_annotation == "List")):
                right = self.visit_List(node.right) if isinstance(node.right, ast.List) else self.visit(node.right)
                return "repeat({0},{1})".format(
                    right, self.visit(node.left)
                )

        # Cover Python list addition
        if isinstance(node.op, ast.Add) :
            right_is_list = node.right.julia_annotation and node.right.julia_annotation == "List"
            left_is_list = node.left.julia_annotation and node.left.julia_annotation == "List"
            if ((isinstance(node.right, ast.List) and isinstance(node.left, ast.List)) 
                    or (isinstance(node.right, ast.Name) and right_is_list and isinstance(node.left, ast.Name) and left_is_list)):
                return f"[{self.visit_List(node.left)};{self.visit_List(node.right)}]"
            
            right_is_string = node.right.julia_annotation and node.right.julia_annotation == "str"
            left_is_string = node.left.julia_annotation and node.left.julia_annotation == "str"
            if ((isinstance(node.right, ast.Str) and isinstance(node.left, ast.Str)) 
                    or (isinstance(node.right, ast.Name) and right_is_string and isinstance(node.left, ast.Name) and left_is_string)):
                return f"{self.visit_List(node.left)}*{self.visit_List(node.right)}"

        if isinstance(node.op, ast.MatMult):
            if(isinstance(node.right, ast.Num) and isinstance(node.left, ast.Num)):
                return "({0}*{1})".format(self.visit(node.left), self.visit(node.right))
        else:
            return super().visit_BinOp(node)

    def visit_ClassDef(self, node) -> str:
        extractor = DeclarationExtractor(JuliaTranspiler())
        extractor.visit(node)
        declarations = node.declarations = extractor.get_declarations()
        node.class_assignments = extractor.class_assignments
        ret = super().visit_ClassDef(node)
        if ret is not None:
            return ret

        decorators_origin = [get_id(d) for d in node.decorator_list]
        decorators = [
            class_for_typename(t, None, self._imported_names) for t in decorators_origin
        ]
        for d in decorators:
            if d in CLASS_DISPATCH_TABLE:
                ret = CLASS_DISPATCH_TABLE[d](self, node)
                if ret is not None:
                    return ret

        decorator_str = ""
        for d in decorators_origin:
            decorator_str = f"# @{d}\n"
        
        if "dataclass" in decorators_origin:
            print(JuliaTranspilerPlugins.visit_argparse_dataclass(self, node))

        fields = []
        index = 0
        for declaration, typename in declarations.items():
            # Allow Julia to infer the types
            # if typename == None:
            #     typename = "ST{0}".format(index)
            #     index += 1
            fields.append(declaration if typename == "" else f"{declaration}::{typename}")

        fields = "" if fields == [] else "\n".join(fields) + "\n"
        struct_def = ""
        if decorator_str and decorator_str != "\n":
            struct_def += decorator_str
        struct_def += f"struct {node.name}\n{fields}end\n"
        for b in node.body:
            if isinstance(b, ast.FunctionDef):
                b.self_type = node.name
        body = "\n".join([self.visit(b) for b in node.body])
        return f"{struct_def}\n{body}"
 
    def _visit_enum(self, node, typename: str, fields: List[Tuple]) -> str:
        decorators = [get_id(d) for d in node.decorator_list]
        field_str = ""
        for field, value in fields:
                field_str += f"\t{field}\n"
        if("unique" in decorators and typename not in INTEGER_TYPES):
            # self._usings.add("Enum")
            return textwrap.dedent(
                f"@enum {node.name}::{typename} begin\n{field_str}end"
            )
        else :
            # Cover case in pyenum where values are unique and strings
            self._usings.add("PyEnum")
            return textwrap.dedent(
                f"@pyenum {node.name}::{typename} begin\n{field_str}end"
            )

    def visit_StrEnum(self, node) -> str:
        fields = []
        for i, (member, var) in enumerate(node.class_assignments.items()):
            var = self.visit(var)
            if var == _AUTO_INVOKED:
                var = f'"{member}"'
            fields.append((member, var))
        return self._visit_enum(node, "String", fields)

    def visit_IntEnum(self, node) -> str:
        fields = []
        for i, (member, var) in enumerate(node.class_assignments.items()):
            var = self.visit(var)
            if var == _AUTO_INVOKED:
                var = i
            fields.append((member, var))
        return self._visit_enum(node, "Int64", fields)

    def visit_IntFlag(self, node) -> str:
        fields = []
        for i, (member, var) in enumerate(node.class_assignments.items()):
            var = self.visit(var)
            if var == _AUTO_INVOKED:
                var = 1 << i
            fields.append((member, var))
        return self._visit_enum(node, "Int64", fields)

    def _import(self, name: str) -> str:
        return f"import {name}"

    # def _import_from(self, module_name: str, names: List[str]) -> str:
    #     if len(names) == 1:
    #         # TODO: make this more generic so it works for len(names) > 1
    #         name = names[0]
    #         lookup = f"{module_name}.{name}"
    #         if lookup in MODULE_DISPATCH_TABLE:
    #             jl_module_name, jl_name = MODULE_DISPATCH_TABLE[lookup]
    #             #jl_module_name = jl_module_name.replace(".", "::")
    #             return f"using {jl_module_name}: {jl_name}"
    #     #module_name = module_name.replace(".", "::")
    #     names = ", ".join(names)
    #     return f"using {module_name}: {names}"

    # New more generic import function
    def _import_from(self, module_name: str, names: List[str]) -> str:
        jl_module_name = module_name
        imports = []
        for name in names:
            lookup = f"{module_name}.{name}"
            if lookup in MODULE_DISPATCH_TABLE:
                jl_module_name, jl_name = MODULE_DISPATCH_TABLE[lookup]
                imports.append(jl_name)
            else:
                imports.append(name)
        str_imports = ", ".join(imports)
        return f"using {jl_module_name}: {str_imports}"

    def visit_List(self, node) -> str:
        elements = [self.visit(e) for e in node.elts]
        elements_str = ", ".join(elements)
        return f"[{elements_str}]"

    def visit_Set(self, node) -> str:
        elements = [self.visit(e) for e in node.elts]
        elements_str = ", ".join(elements)
        return f"Set([{elements_str}])"

    def visit_Dict(self, node) -> str:
        keys = [self.visit(k) for k in node.keys]
        values = [self.visit(k) for k in node.values]
        kv_pairs = ", ".join([f"{k} => {v}" for k, v in zip(keys, values)])
        return f"Dict({kv_pairs})"

    def visit_Subscript(self, node) -> str:
        value = self.visit(node.value)
        index = self.visit(node.slice)
        if index == None:
            return "{0}[(Something, Strange)]".format(value)
        if hasattr(node, "is_annotation"):
            if value in self.CONTAINER_TYPE_MAP:
                value = self.CONTAINER_TYPE_MAP[value]
            if value == "Tuple":
                return "({0})".format(index)
            return "{0}{{{1}}}".format(value, index)
        # TODO: optimize this. We need to compute value_type once per definition
        self._generic_typename_from_annotation(node.value)
        if hasattr(node.value, "annotation"):
            value_type = getattr(node.value.annotation, "generic_container_type", None)
            if value_type is not None and value_type[0] == "List":
                # Julia array indices start at 1
                return "{0}[{1} + 1]".format(value, index)
        return "{0}[{1}]".format(value, index)

    def visit_Index(self, node) -> str:
        return self.visit(node.value)

    def visit_Slice(self, node) -> str:
        lower = "begin"
        if node.lower:
            lower = self.visit(node.lower)
        upper = "end"
        if node.upper:
            upper = self.visit(node.upper)

        return "{0}..{1}".format(lower, upper)

    def visit_Tuple(self, node) -> str:
        elts = [self.visit(e) for e in node.elts]
        elts = ", ".join(elts)
        if hasattr(node, "is_annotation"):
            return elts
        return "({0})".format(elts)

    def visit_Try(self, node, finallybody=None) -> str:
        buf = []
        buf.append("try")
        buf.extend([self.visit(child) for child in node.body])
        if len(node.handlers) > 0:
            buf.append("catch exn")
            for handler in node.handlers:
                buf.append(self.visit(handler))
        if node.finalbody:
            buf.append("finally")
            buf.extend([self.visit(child) for child in node.finalbody])
        buf.append("end")
        return "\n".join(buf)

    def visit_ExceptHandler(self, node) -> str:
        buf = []
        name = "exn"
        if node.name:
            buf.append(f" let {node.name} = {name}")
            name = node.name
        if node.type:
            type_str = self.visit(node.type)
            buf.append(f"if {name} isa {type_str}")
        buf.extend([self.visit(child) for child in node.body])
        if node.type:
            buf.append("end")
        if node.name:
            buf.append("end")
        return "\n".join(buf)

    def visit_Assert(self, node) -> str:
        return "@assert({0})".format(self.visit(node.test))

    def visit_AnnAssign(self, node) -> str:
        target, type_str, val = super().visit_AnnAssign(node)
        if type_str == self._default_type:
            return f"{target} = {val}"
        return f"{target}::{type_str} = {val}"

    def visit_AugAssign(self, node) -> str:
        target = self.visit(node.target)
        op = self.visit(node.op)
        val = self.visit(node.value)
        return "{0} {1}= {2}".format(target, op, val)

    def _visit_AssignOne(self, node, target) -> str:
        if isinstance(target, ast.Tuple):
            elts = [self.visit(e) for e in target.elts]
            value = self.visit(node.value)
            return "{0} = {1}".format(", ".join(elts), value)

        # print(node.scopes[-1].name)
        # print(ast.dump(node.scopes[-1], indent=4))
        if isinstance(node.scopes[-1], ast.If):
            outer_if = node.scopes[-1]
            target_id = self.visit(target)
            if target_id in outer_if.common_vars:
                value = self.visit(node.value)
                return "{0} = {1}".format(target_id, value)

        if isinstance(target, ast.Subscript) or isinstance(target, ast.Attribute):
            target = self.visit(target)
            value = self.visit(node.value)
            if value == None:
                value = "Nothing"
            return "{0} = {1}".format(target, value)

        definition = node.scopes.parent_scopes.find(get_id(target))
        if definition is None:
            definition = node.scopes.find(get_id(target))

        target_str = self.visit(target)
        value = self.visit(node.value)
        expr = f"{target_str} = {value}"
        if isinstance(target, ast.Name) and defined_before(definition, node):
            f"{expr};"
        return expr

    def visit_Delete(self, node) -> str:
        target = node.targets[0]
        return "{0}.drop()".format(self.visit(target))

    def visit_Raise(self, node) -> str:
        if node.exc is not None:
            return "throw({0})".format(self.visit(node.exc))
        # This handles the case where `raise` is used without
        # specifying the exception.
        return "error()"

    def visit_Await(self, node) -> str:
        return "await!({0})".format(self.visit(node.value))

    def visit_AsyncFunctionDef(self, node) -> str:
        return "#[async]\n{0}".format(self.visit_FunctionDef(node))

    def visit_Yield(self, node) -> str:
        return "//yield is unimplemented"

    def visit_Print(self, node) -> str:
        buf = []
        for n in node.values:
            value = self.visit(n)
            buf.append('println("{{:?}}",{0})'.format(value))
        return "\n".join(buf)

    def visit_GeneratorExp(self, node) -> str:
        elt = self.visit(node.elt)
        generators = node.generators
        map_str = ""
        filter_str = ""

        for i in range(len(generators)):
            generator = generators[i]
            target = self.visit(generator.target)
            iter = self.visit(generator.iter)
            map_str += f"{elt} for {target} in {iter}" if i == 0 else f", {target} in {iter}"
            if(len(generator.ifs) == 1):
                filter_str += f"{self.visit(generator.ifs[0])}"
            else:
                for i in range(0, len(generator.ifs)):
                    gen_if = generator.ifs[i]
                    filter_str += f"{self.visit(gen_if)}" if i==0 else f" && {self.visit(gen_if)}"
            
        return f"({map_str} if {filter_str})"

    def visit_ListComp(self, node) -> str:
        return "[" + self.visit_GeneratorExp(node) + "]"

    def visit_DictComp(self, node) -> str:
        key = self.visit(node.key)
        value = self.visit(node.value)
        generator = node.generators[0]
        target = self.visit(generator.target)
        iter = self.visit(generator.iter)

        map_str = "{0}=>{1} for ({0}, {1}) in {2}".format(key, value, iter)
        filter_str = ""
        if generator.ifs:
            filter_str = " if {0}".format(self.visit(generator.ifs[0]))
        return "Dict({0}{1})".format(map_str, filter_str)
    
    def visit_Global(self, node) -> str:
        return "global {0}".format(", ".join(node.names))

    def visit_Starred(self, node) -> str:
        return "starred!({0})/*unsupported*/".format(self.visit(node.value))

    def visit_IfExp(self, node) -> str:
        body = self.visit(node.body)
        orelse = self.visit(node.orelse)
        test = self.visit(node.test)
        return f"{test} ? ({body}) : ({orelse})"
