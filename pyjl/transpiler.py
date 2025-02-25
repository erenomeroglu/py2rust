from copyreg import constructor
from enum import IntFlag
import ast

import re

from py2many.exceptions import AstUnsupportedOperation
from py2many.scope import ScopeList
from pyjl.global_vars import JL_CLASS, OOP_CLASS, RESUMABLE
from py2many.helpers import is_dir, is_file
from pyjl.helpers import verify_types

import pyjl.juliaAst as juliaAst

from .clike import JULIA_INTEGER_TYPES, JULIA_NUM_TYPES, CLikeTranspiler
from .plugins import (
    DECORATOR_DISPATCH_TABLE,
    JULIA_SPECIAL_ASSIGNMENT_DISPATCH_TABLE,
    JULIA_SPECIAL_FUNCTIONS,
    MODULE_DISPATCH_TABLE,
    DISPATCH_MAP,
    SpecialFunctionsPlugins,
)

from py2many.analysis import get_id, is_void_function
from py2many.declaration_extractor import DeclarationExtractor
from py2many.clike import _AUTO_INVOKED
from py2many.tracer import (
    find_in_body,
    find_node_by_name_and_type,
    find_node_by_type,
    find_parent_of_type,
    get_class_scope,
    is_class_or_module,
    is_class_type,
)

from typing import Any, List, Union

SPECIAL_CHARACTER_MAP = {
    "\a": "\\a",
    "\b": "\\b",
    "\f": "\\f",
    "\r": "\\r",
    "\v": "\\v",
    "\t": "\\t",
    "\xe9": "\\xe9",
    "\000": "\\000",
    "\xff": "\\xff",
    "\x00": "\\x00",
    "\0": "\\0",
    "\ud800": "\\ud800",
    "\udfff": "\\udfff",
    "\udcdc": "\\udcdc",
    "\udad1": "\\udad1",
    "\ud8f0": "\\ud8f0",
    "\x80": "\\x80",
}

# For now just includes SPECIAL_CHARACTER_MAP
DOCSTRING_TRANSLATION_MAP = SPECIAL_CHARACTER_MAP

STR_SPECIAL_CHARACTER_MAP = SPECIAL_CHARACTER_MAP | {
    '"': '\\"',
    "'": "\\'",
    "\\": "\\\\",
    "$": "\\$",
    "\n": "\\n",
}

BYTES_SPECIAL_CHARACTER_MAP = SPECIAL_CHARACTER_MAP | {'"': '\\"', "\n": "\\n"}


class JuliaTranspiler(CLikeTranspiler):
    NAME = "julia"

    SPECIAL_METHOD_TABLE = {
        ast.Add: "__add__",
        ast.Sub: "__sub__",
        ast.Mult: "__mul__",
        ast.Div: "__truediv__",
        ast.MatMult: "__matmul__",
        ast.RShift: "__rshift__",
        ast.LShift: "__lshift__",
        ast.Mod: "__mod__",
        ast.FloorDiv: "__floordiv__",
        ast.BitOr: "__or__",
        ast.BitAnd: "__and__",
        ast.BitXor: "__xor__",
        ast.Pow: "__pow__",
    }

    def __init__(self, jl_func_list):
        super().__init__()
        self._headers = set([])
        self._dispatch_map = DISPATCH_MAP

        # Added
        self._julia_num_types = JULIA_NUM_TYPES
        self._julia_integer_types = JULIA_INTEGER_TYPES
        self._str_special_character_map = STR_SPECIAL_CHARACTER_MAP
        self._bytes_special_character_map = BYTES_SPECIAL_CHARACTER_MAP
        self._docstr_special_character_map = DOCSTRING_TRANSLATION_MAP
        self._julia_function_names = jl_func_list

    def comment(self, text: str) -> str:
        return f"#= {text} =#"

    def _combine_value_index(self, value_type, index_type) -> str:
        return f"{value_type}{{{index_type}}}"

    def visit_Name(self, node: ast.Name) -> str:
        node_id = get_id(node)
        if getattr(node, "is_annotation", False) or (
            not getattr(node, "lhs", False)
            and hasattr(node, "scopes")
            and not node.scopes.find(node_id)
            and not getattr(node, "in_call", None)
            and self._map_type(node_id) != node_id
        ):
            return self._map_type(node_id)
        elif node_id in self._julia_keywords and not getattr(
            node, "preserve_keyword", False
        ):
            return f"{node_id}_"
        elif (
            hasattr(node, "lineno")
            and hasattr(node, "scopes")
            and node_id in self._julia_function_names
            and not getattr(node, "preserve_keyword", False)
            and node.scopes.find(node_id)
            and not isinstance(
                node.scopes.find(node_id), (ast.FunctionDef, ast.ClassDef)
            )
            and not isinstance(
                getattr(node.scopes.find(node_id), "assigned_from", None),
                ast.FunctionDef,
            )
        ):
            # Verify if any names override Julia's
            # built-in function names
            return f"{node_id}_"
        elif node_id in self._special_names_dispatch_table:
            return self._special_names_dispatch_table[node_id]
        return super().visit_Name(node)

    def visit_Constant(self, node: ast.Constant, quotes=True, docstring=False) -> str:
        if node.value is True:
            return "true"
        elif node.value is False:
            return "false"
        elif node.value is None:
            if getattr(node, "is_annotation", None):
                return "Nothing"
            else:
                return self._none_type
        elif isinstance(node.value, str):
            return self.visit_Str(node, quotes, docstring)
        elif isinstance(node.value, bytes):
            return self.visit_Bytes(node)
        elif isinstance(node.value, complex):
            str_value = str(node.value)
            return (
                str_value.replace("j", "im") if str_value.endswith("j") else str_value
            )
        else:
            return super().visit_Constant(node)

    def visit_Str(self, node: ast.Str, quotes=True, docstring=False) -> str:
        # Escape special characters
        trs_map = (
            self._str_special_character_map
            if not docstring
            else self._docstr_special_character_map
        )
        node_str = node.value.translate(str.maketrans(trs_map))
        # node_str = node.value.encode("UTF-8").decode("ascii", "backslashreplace") # Avoid special characters
        return f'"{node_str}"' if quotes else node_str

    def visit_Bytes(self, node: ast.Bytes) -> str:
        bytes_str: str = str(node.s)
        content = bytes_str[2:-1]
        bytes_str = content.translate(str.maketrans(self._bytes_special_character_map))
        return f'b"{bytes_str}"'

    def visit_FunctionDef(self, node: ast.FunctionDef) -> str:
        typedecls = []

        # Parse function args
        args_list = self._get_args(node)
        args = ", ".join(args_list)
        node.args_list = args_list
        node.parsed_args = args

        func_generics = set()
        for arg in node.args.args:
            ann = getattr(arg, "annotation", None)
            for g in self._generics:
                if verify_types(ann, g):
                    func_generics.add(g)
        node.maybe_generics = (
            f"where {', '.join(func_generics)}" if func_generics else ""
        )

        # Parse return type
        return_type = ""
        if not is_void_function(node):
            if node.returns:
                func_typename = self._typename_from_annotation(node, attr="returns")
                mapped_type = self._map_type(func_typename)
                if mapped_type:
                    return_type = f"::{self._map_type(func_typename)}"
        node.return_type = return_type

        template = ""
        if len(typedecls) > 0:
            template = "{{{0}}}".format(", ".join(typedecls))
        node.template = template

        # Change functions that have the same name as modules
        if self._use_modules and node.name == self._module:
            node.name = f"{node.name}_"

        # Visit special functions:
        if node.name in JULIA_SPECIAL_FUNCTIONS:
            return JULIA_SPECIAL_FUNCTIONS[node.name](self, node)

        # Visit decorators
        for (d_id, _), decorator in zip(
            node.parsed_decorators.items(), node.decorator_list
        ):
            if d_id in DECORATOR_DISPATCH_TABLE:
                ret = DECORATOR_DISPATCH_TABLE[d_id](self, node, decorator)
                if ret is not None:
                    return ret

        # Visit function body
        docstring_parsed: str = self._get_docstring(node)
        body = [docstring_parsed] if docstring_parsed else []
        body.extend([self.visit(n) for n in node.body])
        body = "\n".join(body)
        if body == "...":
            body = ""

        funcdef = (
            f"function {node.name}{template}({args}){return_type}{node.maybe_generics}"
        )
        return f"{funcdef}\n{body}\nend\n"

    def _get_args(self, node) -> list[str]:
        typenames, args = self.visit(node.args)
        args_list = []

        # Get self type. Only avoid if:
        #  - _oop_nested_funcs is being used, as the @oodef macro will do it automatically.
        #  - the "classmethod" decorator is used
        if (
            len(typenames) > 0
            and (not typenames[0] or typenames[0] == self._default_type)
            and hasattr(node, "self_type")
            and not getattr(node, "_oop_nested_funcs", False)
            and "classmethod" not in node.parsed_decorators
            and "staticmethod" not in node.parsed_decorators
        ):
            if getattr(node, "oop", False):
                typenames[0] = f"@like({node.self_type})"
            else:
                typenames[0] = node.self_type

        defaults = node.args.defaults
        len_defaults = len(defaults)
        len_args = len(args)
        for i in range(len_args):
            arg = args[i]
            arg_typename = typenames[i]

            if arg_typename and arg_typename != "T":
                arg_typename = self._map_type(arg_typename)

            # Get default parameter values
            default = None
            if defaults:
                if len_defaults != len_args:
                    diff_len = len_args - len_defaults
                    default = defaults[i - diff_len] if i >= diff_len else None
                else:
                    default = defaults[i]

            if default is not None:
                default = self.visit(default)

            arg_signature = ""
            if arg_typename and arg_typename != self._default_type:
                arg_signature = (
                    f"{arg}::{arg_typename}"
                    if default is None
                    else f"{arg}::{arg_typename} = {default}"
                )
            else:
                arg_signature = f"{arg}" if default is None else f"{arg} = {default}"
            args_list.append(arg_signature)

        if node.args.vararg:
            _, arg = self.visit(node.args.vararg)
            args_list.append(f"{arg}...")

        return args_list

    def visit_Return(self, node) -> str:
        if node.value:
            return f"return {self.visit(node.value)}"
        return "return"

    def visit_Lambda(self, node) -> str:
        _, args = self.visit(node.args)
        args_string = ", ".join(args)
        node.body.is_lambda_body = True
        body = self.visit(node.body)
        return f"({args_string}) -> {body}"

    def visit_Attribute(self, node) -> str:
        # Account for any attributes referring to variables
        attr = (
            f"{node.attr}_"
            if (
                (
                    node.attr in self._julia_function_names
                    and node.scopes.find(node.attr)
                )
                or node.attr in self._julia_keywords
            )
            else node.attr
        )
        value_id = self.visit(node.value)

        if (dispatch := getattr(node, "dispatch", None)) and not getattr(
            node, "in_call", None
        ):
            dispatch.func.preserve_keyword = True
            fname = self.visit(dispatch.func)
            vargs, _ = self._get_call_args(dispatch)
            ret = self._dispatch(dispatch, fname, vargs, [])
            if ret is not None:
                return ret

        if not value_id:
            value_id = ""

        # If node is an is_annotation or it is not on lhs
        # and is not a variable, it can be mapped
        if getattr(node, "is_annotation", False) or (
            not getattr(node, "lhs", False)
            and hasattr(node, "scopes")
            and not node.scopes.find(f"{value_id}.{attr}")
        ):
            return self._map_type(f"{value_id}.{attr}")

        return f"{value_id}.{attr}"

    def visit_Call(self, node: ast.Call) -> str:
        node.func.in_call = True
        # Change functions that have the same name as modules
        fname = self.visit(node.func)
        if self._use_modules and fname == self._module:
            fname = f"{fname}_"
        vargs, kwargs = self._get_call_args(node)

        ret = self._dispatch(node, fname, vargs, kwargs)
        if ret is not None:
            if isinstance(node.scopes[-1], ast.Try):
                call_func = ret.split("(")[0].split(".")
                c_list = []
                for c in call_func:
                    c_list.append(c)
                    if ".".join(c_list) in self._pycall_imports:
                        self._is_pycall_exception = True
            return ret

        # Check if first arg is of class type.
        # If it is, search for the function in the class scope
        fndef = node.scopes.find(fname)
        if vargs and (
            arg_cls_scope := find_node_by_name_and_type(
                vargs[0], ast.ClassDef, node.scopes
            )[0]
        ):
            fndef = arg_cls_scope.scopes.find(fname)

        if fndef and hasattr(fndef, "args") and getattr(fndef.args, "args", None):
            fndef_args = fndef.args.args
            if "staticmethod" in getattr(fndef, "parsed_decorators", {}) and len(
                vargs
            ) > len(fndef_args):
                # Compensate for self argument
                fndef_args = [ast.arg(arg="self")] + fndef_args
            converted = []
            for varg, fnarg, node_arg in zip(vargs, fndef_args, node.args):
                actual_type = self._typename_from_annotation(node_arg)
                declared_type = self._typename_from_annotation(fnarg)
                if (
                    declared_type
                    and actual_type
                    and declared_type != self._default_type
                    and actual_type != self._default_type
                    and actual_type != declared_type
                    and not actual_type.startswith("Optional")
                ):  # TODO: Skip conversion of Optional for now
                    converted.append(f"convert({declared_type}, {varg})")
                else:
                    converted.append(varg)
        else:
            converted = vargs

        # Join kwargs to converted vargs
        converted.extend([f"{k[0]} = {k[1]}" for k in kwargs])

        args = ", ".join(converted)
        return f"{fname}({args})"

    def _get_call_args(self, node: ast.Call):
        vargs = []
        kwargs = []
        if node.args:
            for a in node.args:
                vargs.append(self.visit(a))
        if node.keywords:
            for n in node.keywords:
                arg_str = n.arg if n.arg not in self._julia_keywords else f"{n.arg}_"
                kwargs.append((arg_str, self.visit(n.value)))
        return vargs, kwargs

    def visit_For(self, node) -> str:
        target = self.visit(node.target)
        it = self.visit(node.iter)
        buf = []

        # Replace square brackets for normal brackets in lhs
        target = target.replace("[", "(").replace("]", ")")
        buf.append(f"for {target} in {it}")
        buf.extend([self.visit(c) for c in node.body])
        buf.append("end")

        return "\n".join(buf)

    def visit_Compare(self, node) -> str:
        left = self.visit(node.left)
        comparators = node.comparators
        ops = node.ops

        comp_exp = []
        for i in range(len(node.comparators)):
            comparator = comparators[i]
            op = ops[i]
            comp_str = self.visit(comparator)
            op_str = self.visit(op)

            if isinstance(op, ast.In) or isinstance(op, ast.NotIn):
                if hasattr(comparator, "annotation"):
                    self._generic_typename_from_annotation(node.comparators[0])
                    value_type = getattr(
                        comparator.annotation, "generic_container_type", None
                    )
                    if value_type and value_type[0] == "Dict":
                        comp_str = f"keys({comp_str})"

            # Isolate composite operations
            if isinstance(comparator, ast.BinOp) or isinstance(comparator, ast.BoolOp):
                comp_str = f"({comp_str})"

            comp_exp.append(f"{op_str} {comp_str}")

        # Isolate composite operations
        if isinstance(node.left, ast.BinOp) or isinstance(node.left, ast.BoolOp):
            left = f"({left})"

        comp_exp = " ".join(comp_exp)
        return f"{left} {comp_exp}"

    def visit_NameConstant(self, node) -> str:
        if node.value is True:
            return "true"
        elif node.value is False:
            return "false"
        elif node.value is None:
            return self._none_type
        else:
            return super().visit_NameConstant(node)

    def visit_If(self, node: ast.If) -> str:
        body_vars = set([get_id(v) for v in node.scopes[-1].body_vars])
        orelse_vars = set([get_id(v) for v in node.scopes[-1].orelse_vars])
        node.common_vars = body_vars.intersection(orelse_vars)

        buf = []
        cond = "if" if not getattr(node, "is_orelse", None) else "elseif"
        buf.append(f"{cond} {self.visit(node.test)}")
        buf.extend([self.visit(child) for child in node.body])

        orelse = node.orelse
        if len(orelse) == 1 and isinstance(orelse[0], ast.If):
            orelse[0].is_orelse = True
            buf.append(self.visit(orelse[0]))
        else:
            if len(orelse) >= 1:
                buf.append("else")
                orelse = [self.visit(child) for child in node.orelse]
                orelse = "\n".join(orelse)
                buf.append(orelse)

        if not getattr(node, "is_orelse", None):
            buf.append("end")

        return "\n".join(buf)

    def visit_While(self, node) -> str:
        buf = []
        buf.append(f"while {self.visit(node.test)}")
        buf.extend([self.visit(n) for n in node.body])
        buf.append("end")
        return "\n".join(buf)

    def visit_BinOp(self, node: ast.BinOp) -> str:
        # Attempts to find node annotations
        left_jl_ann: str = self._typename_from_type_node(
            self._get_annotation(node.left), default=self._default_type
        )
        right_jl_ann: str = self._typename_from_type_node(
            self._get_annotation(node.right), default=self._default_type
        )

        is_list = lambda x: re.match(r"^Array|^Vector", x)
        is_tuple = lambda x: re.match(r"^Tuple", x)
        is_num = lambda x: re.match(r"^Int|^Float", x)

        # Modulo string formatting
        if (
            isinstance(node.left, ast.Constant)
            and isinstance(node.left.value, str)
            and isinstance(node.op, ast.Mod)
        ):
            left = self.visit_Constant(node.left, quotes=False)
            # (?<!%) -> Negative lookbehind (ensures that parsing
            # does not consider "%%"" instances)
            split_str: list[str] = re.split(
                r"(?<!%)%\w|(?<!%)%.\d\w|(?<!%)%-\d\d\w", left
            )
            elts = getattr(node.right, "elts", [node.right])
            for i in range(len(split_str) - 1):
                e = elts[i]
                if isinstance(e, ast.Constant):
                    split_str[i] += self.visit_Constant(e, quotes=False)
                else:
                    split_str[i] += f"$({self.visit(e)})"
            return f"\"{''.join(split_str)}\""

        # Visit left and right
        left = self.visit(node.left)
        right = self.visit(node.right)

        # Handle special method binary operations
        op_type = type(node.op)
        if op_type in self.SPECIAL_METHOD_TABLE:
            new_op: str = self.SPECIAL_METHOD_TABLE[op_type]
            if is_class_type(left, node.scopes):
                return f"{new_op}({left}, {right})"
            elif is_class_type(right, node.scopes):
                op = f"__r{new_op.removeprefix('__')}"
                return f"{op}({right}, {left})"
            elif left.startswith("self"):
                # Prevent infinite recursion
                if node.scopes.find(new_op) and not (
                    isinstance(node.scopes[-1], ast.FunctionDef)
                    and node.scopes[-1].name == new_op
                ):
                    # special methods expect the instance itself
                    return f"{new_op}(self, {right})"

        if isinstance(node.op, ast.Mult):
            # Cover multiplication between List/Tuple and Int
            if isinstance(node.right, ast.Num) or is_num(right_jl_ann):
                if isinstance(node.left, ast.List) and len(node.left.elts) == 1:
                    return f"fill({self.visit(node.left.elts[0])},{right})"
                elif (isinstance(node.left, ast.List) or is_list(left_jl_ann)) or (
                    isinstance(node.left, ast.Str) or left_jl_ann == "String"
                ):
                    return f"repeat({left},{right})"
                elif isinstance(node.left, ast.Tuple) or is_tuple(left_jl_ann):
                    return f"repeat([{left}...],{right})"

            if isinstance(node.left, ast.Num) or is_num(left_jl_ann):
                if isinstance(node.right, ast.List) and len(node.right.elts) == 1:
                    return f"fill({self.visit(node.right.elts[0])},{left})"
                elif (isinstance(node.right, ast.List) or is_list(right_jl_ann)) or (
                    isinstance(node.right, ast.Str) or right_jl_ann == "String"
                ):
                    return f"repeat({right},{left})"
                elif isinstance(node.right, ast.Tuple) or is_tuple(right_jl_ann):
                    return f"repeat([{right}...],{left})"

            # Cover Python Int and Boolean multiplication (also supported in Julia)
            if (
                (
                    isinstance(node.right, ast.Num)
                    or right_jl_ann in self._julia_num_types
                )
                and (isinstance(node.left, ast.BoolOp) or left_jl_ann == "Bool")
            ) or (
                (isinstance(node.left, ast.Num) or left_jl_ann in self._julia_num_types)
                and (isinstance(node.right, ast.BoolOp) or right_jl_ann == "Bool")
            ):
                return f"{left}*{right}"

        if isinstance(node.op, ast.Add):
            # Add two lists
            if (
                isinstance(node.right, ast.List) and isinstance(node.left, ast.List)
            ) or (is_list(right_jl_ann) and is_list(left_jl_ann)):
                return f"[{left}; {right}]"

            # Cover Python String concatenation
            if (isinstance(node.right, ast.Str) and isinstance(node.left, ast.Str)) or (
                right_jl_ann == "String" and left_jl_ann == "String"
            ):
                return f"{left} * {right}"

        # By default, call super
        return super().visit_BinOp(node)

    def _get_annotation(self, node):
        node_id = get_id(node)
        n_inst = node.scopes.find(node_id)
        if hasattr(node, "annotation"):
            return node.annotation
        elif hasattr(n_inst, "annotation"):
            return n_inst.annotation
        elif hasattr(n_inst, "assigned_from") and hasattr(
            n_inst.assigned_from, "annotation"
        ):
            return n_inst.assigned_from.annotation
        return None

    def visit_UnaryOp(self, node: ast.UnaryOp) -> str:
        if isinstance(node.operand, (ast.Call, ast.Num, ast.Name, ast.Constant)):
            # Shortcut if parenthesis are not needed
            return f"{self.visit(node.op)}{self.visit(node.operand)}"
        return super().visit_UnaryOp(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> str:
        dec_ids = node.parsed_decorators.keys()
        # Check validity of function translation
        if OOP_CLASS in dec_ids and JL_CLASS in dec_ids:
            raise AstUnsupportedOperation(
                "Cannot invoke a class using both " "ObjectOriented.jl and Classes.jl",
                node,
            )

        # Extract declarations
        extractor = DeclarationExtractor(self)
        extractor.visit(node)
        node.declarations = extractor.get_declarations()
        node.declarations_with_defaults = extractor.get_declarations_with_defaults()
        node.class_assignments = extractor.class_assignments

        body = []
        for b in node.body:
            if isinstance(b, ast.FunctionDef):
                if b.name == "__init__":
                    node.constructor = SpecialFunctionsPlugins.visit_init(self, b)
                else:
                    body.append(b)
        node.body = body
        body = "\n".join(list(map(self.visit, body)))

        # Get class fields
        self._visit_class_fields(node)

        ret = super().visit_ClassDef(node)
        if ret is not None:
            return ret

        # Visit decorators
        for (d_id, _), decorator in zip(
            node.parsed_decorators.items(), node.decorator_list
        ):
            if d_id in DECORATOR_DISPATCH_TABLE:
                dec_ret = DECORATOR_DISPATCH_TABLE[d_id](self, node, decorator)
                if dec_ret:
                    return dec_ret

        bases = []
        for base in node.jl_bases:
            bases.append(self.visit(base))
        # Build struct definition
        struct_def = (
            f"mutable struct {node.name} <: {', '.join(bases)}"
            if bases
            else f"mutable struct {node.name}"
        )

        docstring = self._get_docstring(node)
        maybe_docstring = f"{docstring}\n" if docstring else ""
        maybe_constructor = (
            f"\n{self.visit(node.constructor)}"
            if getattr(node, "constructor", None)
            else ""
        )

        return f"{struct_def}\n{maybe_docstring}{node.fields_str}{maybe_constructor}\nend\n{body}"

    def _visit_class_fields(self, node: ast.ClassDef):
        declarations: dict[str, (str, Any)] = node.declarations_with_defaults

        if (
            JL_CLASS not in node.parsed_decorators
            and OOP_CLASS not in node.parsed_decorators
        ):
            # If we are using composition and the class extends from super,
            # it must contain its fields as well.
            for cls in node.bases:
                base_node = node.scopes.find(get_id(cls))
                if decls := getattr(base_node, "declarations_with_defaults", None):
                    for decl_id, (t_name, val) in decls.items():
                        if decl_id not in declarations:
                            node.declarations[decl_id] = t_name
                            node.declarations_with_defaults[decl_id] = (t_name, val)

        dec_items = []
        has_defaults = False
        if not getattr(node, "constructor_args", None):
            items = list(declarations.items())
            # Cover any class assignments
            for name, value in node.class_assignments.items():
                scopes = getattr(value, "scopes", None)
                if (
                    scopes
                    and isinstance(scopes[-1], ast.ClassDef)
                    and name not in declarations
                ):
                    typename = getattr(value, "annotation", None)
                    typename = self.visit(typename) if typename else None
                    items.append((name, (typename, value)))
            # Check if there are default values
            has_defaults = any(list(map(lambda x: x[1][1] is not None, items)))
            # Reorganize fields, so that defaults are last
            dec_items = (
                sorted(items, key=lambda x: (x[1][1] != None, x), reverse=False)
                if has_defaults
                else items
            )
        else:
            # Sort args according to constructor
            init_args = (
                node.constructor_args.args if hasattr(node, "constructor_args") else []
            )
            args_str = []
            for arg in init_args:
                arg_str = arg.arg
                args_str.append(arg_str)
                if arg_str in declarations:
                    typename, default = declarations[arg_str]
                    # Attempt to propagate types
                    if (not typename or typename == self._default_type) and (
                        arg_ann := getattr(arg, "annotation", None)
                    ):
                        typename = self.visit(arg_ann)
                    dec_items.append((arg_str, (typename, default)))
            # Add leftover args
            for name, (typename, default) in declarations.items():
                if name not in args_str:
                    dec_items.append((name, (typename, default)))

        check_reserved_names = (
            lambda item: (f"{item[0]}_", item[1])
            if (item[0] in self._julia_function_names)
            else (item[0], item[1])
        )

        dec_items = list(map(check_reserved_names, dec_items))

        decs = []
        fields = []
        fields_str = []
        for declaration, (typename, default) in dec_items:
            dec = declaration.split(".")
            if dec[0] == "self" and len(dec) > 1:
                declaration = dec[1]
            if declaration in self._julia_keywords:
                declaration = f"{declaration}_"

            if is_class_or_module(typename, node.scopes):
                typename = f"Abstract{typename}"

            decs.append(declaration)
            fields_str.append(
                declaration
                if (not typename or (typename and typename == self._default_type))
                else f"{declaration}::{typename}"
            )

            # Default field values
            fields.append((declaration, typename, default))

        if not hasattr(node, "constructor_args") and has_defaults:
            node.constructor = self._build_constructor(node, dec_items)

        node.fields = fields
        node.fields_str = "\n".join(fields_str)

    def _build_constructor(self, node: ast.ClassDef, dec_items: dict[str, Any]):
        args = ast.arguments(args=[], defaults=[])
        assigns = []
        declarations = []
        for declaration, (typename, default_node) in dec_items:
            args.args.append(ast.arg(arg=declaration, annotation=ast.Name(id=typename)))
            if isinstance(default_node, ast.Name) and not node.scopes.find(
                get_id(default_node)
            ):
                args.defaults.append(ast.Constant(value=None))
            else:
                args.defaults.append(default_node)
            declarations.append(ast.Name(id=declaration))
            assigns.append(
                ast.Assign(
                    targets=[ast.Name(id=declaration)], value=ast.Name(id=declaration)
                )
            )
        is_oop = OOP_CLASS in node.parsed_decorators
        if is_oop:
            constructor = ast.FunctionDef(
                name="new",
                args=args,
                body=[],
            )
        else:
            constructor = juliaAst.Constructor(
                name=node.name,
                args=args,
                body=[],
            )
        constructor.body = assigns
        obj_builder = ast.Call(
            func=ast.Name(id="new"), args=declarations, keywords=[], scopes=ScopeList()
        )
        ast.fix_missing_locations(obj_builder)
        constructor.body.append(obj_builder)
        constructor.scopes = node.scopes
        constructor.parsed_decorators = {}
        constructor.decorator_list = []
        ast.fix_missing_locations(constructor)
        return constructor

    def visit_StrEnum(self, node) -> str:
        return self._visit_enum(node, "String", str)

    def visit_IntEnum(self, node) -> str:
        return self._visit_enum(node, "Int", int)

    def visit_IntFlag(self, node: IntFlag) -> str:
        return self._visit_enum(node, "Int", IntFlag)

    def _visit_enum(self, node, typename: str, caller_type) -> str:
        declarations = node.class_assignments.items()
        sep = "=>" if typename == "String" else "="

        fields = []
        for i, (field, value) in enumerate(declarations):
            val = self.visit(value)
            if val == _AUTO_INVOKED:
                if caller_type == IntFlag:
                    val = 1 << i
                elif caller_type == int:
                    val = i
                elif caller_type == str:
                    val = f'"{value}"'
            fields.append(f"{field} {sep} {val}")
        field_str = "\n".join(fields)
        # Use SuperEnum to translate
        self._usings.add("SuperEnum")
        return f"@se {node.name} begin\n{field_str}\nend\n"

    def _import(self, name: str, alias: str) -> str:
        """Formatting import"""
        self._usings.add("FromFile: @from")
        import_str = self._get_file_import(name)
        if import_str:
            if self._use_modules:
                # Module has the same name as file
                mod_name = name.split(".")[-1]
                if alias and alias != mod_name:
                    return f"{import_str}: {mod_name} as {alias}"
            return import_str

        if name in MODULE_DISPATCH_TABLE:
            name = MODULE_DISPATCH_TABLE[name]
        import_str = f"import {name}"
        return f"{import_str} as {alias}" if alias else import_str

    def _import_from(
        self, module_name: str, names: List[tuple[str, str]], level: int = 0
    ) -> str:
        """Formatting import from"""
        self._usings.add("FromFile: @from")
        if module_name == ".":
            import_names = []
            for n in names:
                name = n[0]
                import_names.append(f'@from "{name}.jl" using {name}')
            return "\n".join(import_names)

        include_stmts = []
        if level > 0:
            dirs = "../" * (level - 1)
            imp_names = self._retrieve_import_names(names)
            module_name = "/".join(module_name.split("."))
            import_str = f'@from "{dirs}{module_name}.jl" using {module_name}'
            if imp_names:
                return f"{import_str}: {', '.join(imp_names)}"
            return import_str

        if is_dir(module_name, self._basedir):
            import_names = []
            for name, alias in names:
                lookup = f"{module_name}.{name}"
                if imp := self._get_file_import(lookup):
                    # Name is a module
                    include_stmts.append(imp)
                else:
                    # Imports to __init__
                    if alias:
                        import_names.append(f"{name} as {alias}")
                    else:
                        import_names.append(name)
            init_imp = self._get_file_import(f"{module_name}.__init__")
            if import_names:
                include_stmts.append(f"{init_imp}: " + f"{', '.join(import_names)}")
            return "\n".join(include_stmts)
        elif is_file(module_name, self._basedir):
            imp_str = self._get_file_import(module_name)
            import_names = self._retrieve_import_names(names)
            if import_names:
                return f"{imp_str}: {', '.join(import_names)}"
            return imp_str
        else:
            # External modules
            names = self._retrieve_import_names(names)
            jl_module_name = module_name
            if module_name in MODULE_DISPATCH_TABLE:
                jl_module_name = MODULE_DISPATCH_TABLE[module_name]
            if names:
                return f"using {jl_module_name}: {', '.join(names)}"
            return f"using {jl_module_name}"

    def _retrieve_import_names(self, names: list[tuple[str, str]]):
        import_names = []
        for name, alias in names:
            if alias:
                import_names.append(f"{name} as {alias}")
            else:
                import_names.append(name)
        return import_names

    def _get_file_import(self, name: str):
        """Check if name is a path to a file and returns
        the resulting import string if it succeeds"""
        if (id_mod := is_file(name, self._basedir)) or (
            is_path := is_dir(name, self._basedir)
        ):
            # Find path from current file
            sep = "/"
            extension = ".jl" if id_mod else "/__init__.jl"
            import_path = name.split(".")
            # Rewrite __init__ module names
            mod_name = (
                import_path[-2] if import_path[-1] == "__init__" else import_path[-1]
            )
            out_filepath = self._filename.as_posix().split("/")[:-1]
            # Handle cases where out_filepath contains basedir parts
            base_dir_split = self._basedir.as_posix().split("/")
            idx = 0
            for elem, base_elem in zip(out_filepath, base_dir_split):
                if elem == base_elem:
                    idx += 1
            # Parse final import Path
            if import_path[0] == self._basedir.stem:
                import_path = import_path[1:]
                # Temporary solution
                if len(import_path) == 1:
                    # We want the root directory
                    rev_path = "../" * len(out_filepath)
                    parsed_path = sep.join(import_path)
                    return (
                        f'@from "{rev_path}{parsed_path}{extension}" using {mod_name}'
                    )

            if out_filepath == import_path[:-1]:
                # Shortcut if paths are the same
                return f'@from "{import_path[-1]}{extension}" using {mod_name}'
            i, j = -1, -1
            rev_cnt = 0
            while i >= -len(import_path) and j >= -len(out_filepath):
                if import_path[i] != out_filepath[j]:
                    rev_cnt += 1
                else:
                    break
                i, j = i - 1, j - 1
            if rev_cnt > 0:
                # Get relative path from current file
                rev_path = "../" * rev_cnt
                parsed_path = sep.join(import_path)
                return f'@from "{rev_path}{parsed_path}{extension}" using {mod_name}'
            return f'@from "{sep.join(import_path)}{extension}" using {mod_name}'
        return None

    def visit_List(self, node: ast.List) -> str:
        elts = self._parse_elts(node)
        if hasattr(node, "is_annotation"):
            return f"{{{elts}}}"
        return f"({elts})" if hasattr(node, "lhs") and node.lhs else f"[{elts}]"

    def visit_Tuple(self, node: ast.Tuple) -> str:
        elts = self._parse_elts(node)
        if hasattr(node, "is_annotation"):
            return elts
        if len(node.elts) == 1:
            return f"({elts},)"
        return f"({elts})"

    def _parse_elts(self, node):
        elts = []
        for e in node.elts:
            e_str = self.visit(e)
            if getattr(e, "is_annotation", False):
                elts.append(self._map_type(e_str))
            else:
                elts.append(e_str)
        return ", ".join(elts)

    def visit_Set(self, node) -> str:
        elements = [self.visit(e) for e in node.elts]
        elements_str = ", ".join(elements)
        return f"Set([{elements_str}])"

    def visit_Dict(self, node) -> str:
        kv_pairs = []
        for key, value in zip(node.keys, node.values):
            if key:
                kv_pairs.append(f"{self.visit(key)} => {self.visit(value)}")
            else:
                if isinstance(value, ast.Dict):
                    kv_pairs.append(f"{self.visit(value)}...")
        kv_pairs = ", ".join(kv_pairs)

        # Get the annotation
        maybe_ann = ""
        if hasattr(node, "annotation") and isinstance(node.annotation, ast.Subscript):
            # node.annotation.slice.is_annotation = True
            maybe_ann = f"{{{self.visit(node.annotation.slice)}}}"
        if not maybe_ann and hasattr(node, "container_type"):
            maybe_ann = f"{{{node.container_type[1]}}}"
        return f"Dict{maybe_ann}({kv_pairs})"

    def visit_Subscript(self, node) -> str:
        value = self.visit(node.value)
        index = self.visit(node.slice)
        if hasattr(node, "is_annotation"):
            value_type = self._map_type(value)
            index_type = self._map_type(index)
            if value == "Optional":
                return f"Union{{{index_type}, Nothing}}"
            return f"{value_type}{{{index_type}}}"

        return f"{value}[{index}]"

    def visit_Index(self, node) -> str:
        return self.visit(node.value)

    def visit_Slice(self, node) -> str:
        lower = "begin"
        if node.lower:
            lower = self.visit(node.lower)
        upper = "end"
        if node.upper:
            upper = self.visit(node.upper)

        if getattr(node, "step", None):
            step = self.visit(node.step)
            if isinstance(node.step, ast.UnaryOp) and isinstance(
                node.step.op, ast.USub
            ):
                return f"{upper}:{step}:{lower}"
            else:
                return f"{lower}:{step}:{upper}"

        return f"{lower}:{upper}"

    def visit_Ellipsis(self, node: ast.Ellipsis) -> str:
        return "..."

    def visit_Try(self, node, finallybody=None) -> str:
        buf = []
        buf.append("try")
        self._is_pycall_exception = False
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
            if self._is_pycall_exception:
                type_str = self._map_type(self.visit(node.type))
                buf.append(
                    f'if {name} isa PyCall.PyError && pybuiltin(:issubclass)({name}.T, py"{type_str}")'
                )
            else:
                type_str = self._map_type(self.visit(node.type))
                buf.append(f"if {name} isa {type_str}")
        buf.extend([self.visit(child) for child in node.body])
        if node.type:
            buf.append("end")
        if node.name:
            buf.append("end")
        return "\n".join(buf)

    def visit_Assert(self, node) -> str:
        return "@assert({0})".format(self.visit(node.test))

    def visit_AnnAssign(self, node: ast.AnnAssign) -> str:
        if (val := self._generic_assign_visit(node, node.target)) == "":
            return val

        target = self.visit(node.target)
        type_str = self._typename_from_type_node(node.annotation)

        val = None
        if node.value is not None:
            val = self.visit(node.value)

        parent_scope = node.scopes[-1]
        if (
            isinstance(parent_scope, ast.Module)
            or getattr(parent_scope, "is_python_main", False)
        ) and not self._allow_annotations_on_globals:
            type_str = None

        if val:
            if not type_str or type_str == self._default_type:
                return f"{target} = {val}"
            return f"{target}::{type_str} = {val}"
        else:
            if not type_str or type_str == self._default_type:
                return f"{target}"
            return f"{target}::{type_str}"

    def visit_AugAssign(self, node: ast.AugAssign) -> str:
        target = self.visit(node.target)
        op = self.visit(node.op)
        val = self.visit(node.value)

        # Use special methods if it is a class instance
        if class_node := get_class_scope(target, node.scopes):
            op_type = type(node.op)
            if op_type in self.SPECIAL_METHOD_TABLE:
                new_op: str = f"{self.SPECIAL_METHOD_TABLE[op_type]}"
                aug_op = f"__i{new_op.removeprefix('__')}"
                if find_in_body(
                    class_node.body,
                    lambda x: isinstance(x, ast.FunctionDef) and x.name == aug_op,
                ):
                    return f"{target} = {aug_op}({target}, {val})"
                else:
                    # Fallback to normal operation
                    return f"{target} = {new_op}({target}, {val})"
        return "{0} {1}= {2}".format(target, op, val)

    def visit_Assign(self, node: ast.Assign) -> str:
        if (val := self._generic_assign_visit(node, node.targets[0])) == "":
            return val

        value = self.visit(node.value)
        if len(node.targets) == 1:
            if (
                target := self.visit(node.targets[0])
            ) in JULIA_SPECIAL_ASSIGNMENT_DISPATCH_TABLE:
                return JULIA_SPECIAL_ASSIGNMENT_DISPATCH_TABLE[target](
                    self, node, value
                )

        # Allow inferred annotations on assignments
        parent_scope = node.scopes[-1]
        no_globals = (
            isinstance(parent_scope, ast.Module)
            or getattr(parent_scope, "is_python_main", False)
        ) and not self._allow_annotations_on_globals
        targets = []
        for t in node.targets:
            if (
                hasattr(t, "annotation")
                and not no_globals
                and not isinstance(t, ast.Tuple)
                and getattr(t, "make_generic", None)
            ):
                targets.append(f"{self.visit(t)}::{self.visit(t.annotation)}")
            else:
                targets.append(self.visit(t))

        if value == None:
            value = self._none_type

        # Custom type comments to preserve literal values
        if value is not None and value.isdigit():
            value = int(value)
            if ann := get_id(getattr(node, "annotation", None)):
                if ann == "BLiteral":
                    value = bin(value)
                if ann == "OLiteral":
                    value = oct(value)
                if ann == "HLiteral":
                    value = hex(value)

        op = f".=" if getattr(node, "broadcast", False) else "="

        # Optimization to use global constants
        if getattr(node, "use_constant", None):
            return f"const {targets[0]} {op} {value}"

        # Support for local variables
        if getattr(node, "local", None):
            return f"local {'='.join(targets)} {op} {value}"

        return f"{'='.join(targets)} {op} {value}"

    def _generic_assign_visit(self, node: Union[ast.AnnAssign, ast.Assign], target):
        # Removing special TypeVar nodes
        if isinstance(node.value, ast.Call) and get_id(node.value.func) == "TypeVar":
            if (
                isinstance(node.value.args[0], ast.Constant)
                and node.value.args[0].value not in self._reserved_typevars
            ):
                self._generics.append(get_id(target))
            return ""
        return None

    def visit_Delete(self, node: ast.Delete) -> str:
        del_targets = []
        for t in node.targets:
            # Get node name
            if isinstance(t, ast.Subscript):
                node_name = self.visit(t.value)
            else:
                node_name = self.visit(t)

            type_ann = None
            if t_ann := getattr(t, "annotation", None):
                type_ann = t_ann
            elif n_ann := getattr(node.scopes.find(node_name), "annotation", None):
                type_ann = n_ann
            if isinstance(t, ast.Subscript):
                node_name = self.visit(t.value)

            if type_ann and (ann_str := self.visit(type_ann)):
                if isinstance(t, ast.Subscript):
                    if ann_str.startswith("Dict"):
                        del_targets.append(
                            f"delete!({node_name}, {self.visit(t.slice)})"
                        )
                    elif ann_str.startswith("Vector") or ann_str.startswith("Array"):
                        del_targets.append(
                            f"deleteat!({node_name}, {self.visit(t.slice)})"
                        )

        if not del_targets:
            del_targets.extend(["# Delete Unsupported", f"# del({node_name})"])

        return "\n".join(del_targets)

    def visit_Raise(self, node) -> str:
        if node.exc is not None:
            return "throw({0})".format(self.visit(node.exc))
        # This handles the case where `raise` is used without
        # specifying the exception.
        return "error()"

    def visit_Await(self, node) -> str:
        return f"wait({self.visit(node.value)})"

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> str:
        # Parse function args
        args_list = self._get_args(node)
        args = ", ".join(args_list)

        body = []
        for n in node.body:
            body.append(self.visit(n))
        body = "\n".join(body)

        if hasattr(node, "returns") and node.returns:
            f"@async function {node.name} ({args})::{self.visit(node.returns)}\n{body}end"

        return f"@async function {node.name}({args})\n{body}\nend"

    def visit_Yield(self, node: ast.Yield) -> str:
        func_scope = find_node_by_type(ast.FunctionDef, node.scopes)
        if func_scope:
            if "contextlib.contextmanager" in func_scope.parsed_decorators:
                # Using DataTypesBasic package
                # If there is no value, use the default set by
                #   "visit_contextmanager" in "pyjl/plugins"
                return (
                    f"res = cont({self.visit(node.value)})"
                    if node.value
                    else "res = cont()"
                )
            if RESUMABLE in func_scope.parsed_decorators:
                # Using resumables package
                return f"@yield {self.visit(node.value)}" if node.value else "@yield"
            else:
                if node.value:
                    return f"put!(ch_{func_scope.name}, {self.visit(node.value)})"
                else:
                    if getattr(node, "is_lambda_body", False):
                        raise AstUnsupportedOperation(
                            "yield cannot be used withing anonymous functions", node
                        )
                    else:
                        raise AstUnsupportedOperation(
                            "yield requires a value when using channels", node
                        )

    def visit_YieldFrom(self, node: ast.YieldFrom) -> str:
        # Currently not supported
        return f"# Unsupported\n# yield_from {self.visit(node.value)}"

    def visit_Print(self, node) -> str:
        buf = []
        for n in node.values:
            value = self.visit(n)
            buf.append('println("{{:?}}",{0})'.format(value))
        return "\n".join(buf)

    def visit_GeneratorExp(self, node) -> str:
        elt = self.visit(node.elt)
        generators = node.generators
        gen_expr = self._visit_generators(generators)
        return f"({elt} {gen_expr})"

    def visit_ListComp(self, node) -> str:
        elt = self.visit(node.elt)
        generators = node.generators
        list_comp = self._visit_generators(generators)
        return f"[{elt} {list_comp}]"

    def visit_DictComp(self, node: ast.DictComp) -> str:
        key = self.visit(node.key)
        value = self.visit(node.value)
        generators = node.generators
        dict_comp = f"{key} => {value} " + self._visit_generators(generators)

        return f"Dict({dict_comp})"

    def _visit_generators(self, generators):
        gen_exp = []
        for i in range(len(generators)):
            generator = generators[i]
            target = self.visit(generator.target)
            iter = self.visit(generator.iter)
            exp = f"for {target} in {iter}"
            gen_exp.append(exp) if i == 0 else gen_exp.append(f" {exp}")
            filter_str = []
            if len(generator.ifs) == 1:
                filter_str.append(f" if {self.visit(generator.ifs[0])} ")
            else:
                for i in range(0, len(generator.ifs)):
                    gen_if = generator.ifs[i]
                    filter_str.append(
                        f" if {self.visit(gen_if)}"
                        if i == 0
                        else f" && {self.visit(gen_if)} "
                    )
            gen_exp.extend(filter_str)

        return "".join(gen_exp)

    def visit_Global(self, node) -> str:
        return "global {0}".format(", ".join(node.names))

    def visit_Starred(self, node) -> str:
        return f"{self.visit(node.value)}..."

    def visit_IfExp(self, node) -> str:
        body = self.visit(node.body)
        orelse = self.visit(node.orelse)
        test = self.visit(node.test)
        return f"{test} ? ({body}) : ({orelse})"

    def visit_JoinedStr(self, node: ast.JoinedStr) -> str:
        str_repr = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                str_repr.append(self.visit_Constant(value, quotes=False))
            elif isinstance(value, ast.FormattedValue):
                val_str = self.visit(value)
                if re.match(r"^'.*'$", val_str):
                    val_str = val_str[1:-1]
                str_repr.append(f"$({val_str})")
            else:
                str_repr.append(self.visit(value))
        return f"\"{''.join(str_repr)}\""

    def visit_FormattedValue(self, node: ast.FormattedValue) -> Any:
        value = self.visit(node.value)
        conversion = node.conversion
        if conversion == 115:
            value = str(value)
        elif conversion == 114:
            value = repr(value)
        elif conversion == 94:
            value = ascii(value)

        if f_spec_val := getattr(node, "format_spec", None):
            f_spec_val: str = self.visit(f_spec_val)
            # Supporting rounding
            if re.match(r".[\d]*", f_spec_val):
                f_spec_split = f_spec_val.split(".")
                if len(f_spec_split) > 1:
                    f_spec_parsed = f_spec_split[1].replace('"', "")
                else:
                    f_spec_parsed = f_spec_val
                return f"round({value}, digits={f_spec_parsed})"

        return f"{value}"

    def visit_With(self, node: ast.With) -> Any:
        items = [self.visit(it) for it in node.items]
        items = "; ".join(items)
        body = [self.visit(n) for n in node.body]
        body = "\n".join(body)
        return f"{items} \n{body}\nend"

    def visit_withitem(self, node: ast.withitem) -> Any:
        cntx_data = self.visit(node.context_expr)
        if hasattr(node, "optional_vars") and node.optional_vars:
            optional = self.visit(node.optional_vars)
            return f"{cntx_data} do {optional}"
        return f"{cntx_data} do"

    def visit_Nonlocal(self, node: ast.Nonlocal) -> str:
        names = ", ".join(node.names)
        return f"# Not Supported\n# nonlocal {names}"

    def visit_BoolOp(self, node):
        op = self.visit(node.op)
        return f"({op.join([self.visit(v) for v in node.values])})"

    ######################################################
    #################### Julia Nodes #####################
    ######################################################

    def visit_AbstractType(self, node: juliaAst.AbstractType) -> Any:
        name = self.visit(node.value)
        extends = None
        if node.extends is not None:
            extends = self.visit(node.extends)
        return (
            f"abstract type Abstract{name} end"
            if extends is None
            else f"abstract type Abstract{name} <: {extends} end"
        )

    def visit_LetStmt(self, node: juliaAst.LetStmt) -> Any:
        args = [self.visit(arg) for arg in node.args]
        args_str = ", ".join(args)

        # Visit let body
        body = []
        for n in node.body:
            body.append(self.visit(n))
        body = "\n".join(body)

        return f"let {args_str}\n{body}\nend"

    def visit_JuliaModule(self, node: juliaAst.JuliaModule) -> Any:
        body = self.visit_Module(node)
        mod_name = self.visit(node.name)
        parent_dir = (
            self._filename.parent.stem
            if self._filename.parent.stem
            else self._basedir.stem
        )
        if mod_name == "__init__" and parent_dir:
            return f"module {parent_dir}\n{body}\nend"
        return f"module {mod_name}\n{body}\nend"

    def visit_OrderedDict(self, node: juliaAst.OrderedDict):
        self._usings.add("OrderedCollections")
        kv_pairs = []
        for key, value in zip(node.keys, node.values):
            if key:
                kv_pairs.append(f"{self.visit(key)} => {self.visit(value)}")
            else:
                if isinstance(value, ast.Dict):
                    kv_pairs.append(f"{self.visit(value)}...")

        kv_pairs = ", ".join(kv_pairs)
        return f"OrderedDict({kv_pairs})"

    def visit_OrderedDictComp(self, node: juliaAst.OrderedDictComp):
        self._usings.add("OrderedCollections")
        key = self.visit(node.key)
        value = self.visit(node.value)
        generators = node.generators
        dict_comp = f"{key} => {value} " + self._visit_generators(generators)

        return f"OrderedDict({dict_comp})"

    def visit_OrderedSet(self, node: juliaAst.OrderedDict):
        self._usings.add("OrderedCollections")
        elements = [self.visit(e) for e in node.elts]
        elements_str = ", ".join(elements)
        return f"OrderedSet([{elements_str}])"

    def visit_Constructor(self, node: juliaAst.Constructor):
        args = self._get_args(node)
        if args and args[0].split("::")[0] == "self":
            # Remove self
            args = args[1:]
        args_str = ", ".join(args)

        docstring_parsed: str = self._get_docstring(node)
        body = [docstring_parsed] if docstring_parsed else []
        body.extend([self.visit(n) for n in node.body])
        body = "\n".join(body)

        if (
            len(node.body) == 1
            and isinstance(node.body[0], ast.Call)
            and get_id(node.body[0].func) == "new"
        ):
            return f"{node.name}({args_str}) = {body}"
        else:
            return f"""{node.name}({args_str}) = begin
                            {body}
                        end"""

    def visit_Block(self, node: juliaAst.Block) -> Any:
        body = []
        for n in node.body:
            body.append(self.visit(n))
        body = "\n".join(body)
        if getattr(node, "decorator_list", None):
            decorators = " ".join(
                list(map(lambda x: f"@{self.visit(x)}", node.decorator_list))
            )
            return f"{decorators} {node.name} begin\n{body}\nend"
        block_type = getattr(node, "block_type", None)
        if block_type == "expression_block":
            return f"{self.visit(node.block_expr)} begin\n{body}\nend"
        # By default, use named
        return f"{node.name} begin\n{body}\nend"

    def visit_Symbol(self, node: juliaAst.Symbol) -> Any:
        return f":{node.id}"

    def visit_JuliaLambda(self, node: juliaAst.JuliaLambda):
        args = ", ".join(self._get_args(node))
        body = []
        for n in node.body:
            body.append(self.visit(n))
        body = "\n".join(body)
        return f"({args}) -> begin\n{body}\nend"

    def visit_InlineFunction(self, node: juliaAst.InlineFunction):
        args = ", ".join(self._get_args(node))
        body = self.visit(node.body[0])
        return f"{node.name}({args}) = {body}"
