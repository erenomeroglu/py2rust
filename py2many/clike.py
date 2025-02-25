import ast
import logging
import math
import os
import random
import sys
import time

from pathlib import Path
from typing import Any, Dict, OrderedDict

# Fixed width ints and aliases
from ctypes import (
    c_int8 as i8,
    c_int16 as i16,
    c_int32 as i32,
    c_int64 as i64,
    c_uint8 as u8,
    c_uint16 as u16,
    c_uint32 as u32,
    c_uint64 as u64,
)

from py2many.helpers import get_ann_repr

ilong = i64
ulong = u64
isize = i64
usize = u64
c_int8 = i8
c_int16 = i16
c_int32 = i32
c_int64 = i64
c_uint8 = u8
c_uint16 = u16
c_uint32 = u32
c_uint64 = u64


from py2many.analysis import get_id, IGNORED_MODULE_SET
from py2many.astx import LifeTime
from py2many.exceptions import (
    AstCouldNotInfer,
    AstEmptyNodeFound,
    AstNotImplementedError,
    AstTypeNotSupported,
    TypeNotSupported,
)
from py2many.result import Result
from typing import List, Optional, Tuple, Union

os.path  # silence pyflakes
math.pi  # silence pyflakes
time.time  # silence pyflakes
random.random  # silence pyflakes
Result  # silence pyflakes

symbols = {
    ast.Eq: "==",
    ast.Is: "==",
    ast.NotEq: "!=",
    ast.Mult: "*",
    ast.Add: "+",
    ast.Sub: "-",
    ast.Div: "/",
    ast.FloorDiv: "/",
    ast.Mod: "%",
    ast.Lt: "<",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.LtE: "<=",
    ast.LShift: "<<",
    ast.RShift: ">>",
    ast.BitXor: "^",
    ast.BitOr: "|",
    ast.BitAnd: "&",
    ast.Not: "!",
    ast.IsNot: "!=",
    ast.USub: "-",
    ast.UAdd: "+",
    ast.And: "&&",
    ast.Or: "||",
    ast.In: "in",
}

DEFAULT_CONTAINER_MAP = {
    "list": "list",
    "List": "List",
    "Dict": "Dict",
    "Set": "Set",
    "Tuple": "Tuple",
    "tuple": "tuple",
    "Optional": "Optional",
    "bytearray": "bytearray",
}

_AUTO = "auto"
_AUTO_INVOKED = "auto()"


logger = logging.Logger("py2many")


def class_for_typename(typename: str, default_type, locals=None) -> Union[str, object]:
    if typename is None:
        return None
    if typename == "super" or typename.startswith("super()"):
        # Cant eval super; causes RuntimeError
        return None
    try:
        typeclass = eval(typename, globals(), locals)
        if hasattr(typeclass, "__self__") and not isinstance(
            typeclass.__self__, type(sys)
        ):
            # Method of an instance instead of a class.
            return getattr(typeclass.__self__.__class__, typeclass.__name__)

        if not isinstance(typeclass, (type, type(open), type(class_for_typename))):
            return typeclass.__class__
        return typeclass
    except (NameError, SyntaxError, AttributeError, TypeError):
        logger.info(f"could not evaluate {typename}")
        return default_type


def c_symbol(node):
    """Find the equivalent C symbol for a Python ast symbol node"""
    symbol_type = type(node)
    return symbols[symbol_type]


class CLikeTranspiler(ast.NodeVisitor):
    """Provides a base for C-like programming languages"""

    NAME: str

    builtin_constants = frozenset(["True", "False"])

    def __init__(self):
        self._type_map = {}
        self._headers = set([])
        self._usings = set([])
        self._imported_names: Dict[str, Any] = {}
        self._features = set([])
        self._imports = []
        self._basedir = None
        self._filename = None
        self._container_type_map = DEFAULT_CONTAINER_MAP
        self._default_type = _AUTO
        self._statement_separator = ";"
        self._main_signature_arg_names = []
        self._extension = False
        self._ignored_module_set = IGNORED_MODULE_SET.copy()
        self._dispatch_map = {}
        self._small_dispatch_map = {}
        self._small_usings_map = {}
        self._func_dispatch_table = {}
        self._func_usings_map = {}
        self._attr_dispatch_table = {}
        self._keywords = {}
        self._throw_on_unimplemented = True
        #
        self._module = None
        self._globals = set([])
        self._external_type_map = {}
        self._module_dispatch_table = {}

    def headers(self, meta=None):
        return "\n".join(self._headers)

    def usings(self):
        return "\n".join(self._usings)

    def globals(self):
        return "\n".join(self._globals)

    def features(self):
        return "\n".join(self._features)

    @property
    def extension(self):
        return self._extension

    def extension_module(self) -> str:
        return ""

    def comment(self, text):
        return f"/* {text} */"

    def _cast(self, name: str, to) -> str:
        return f"({to}) {name}"

    def _slice_value(self, node: ast.Subscript):
        # 3.9 compatibility shim
        if sys.version_info < (3, 9, 0):
            if isinstance(node.slice, ast.Index):
                slice_value = node.slice.value
            else:
                slice_value = node.slice
        else:
            slice_value = self.visit(node.slice)
        return slice_value

    def visit_Slice(self, node: ast.Slice) -> Any:
        # Simple slice translation
        lower, upper, step = "", "", ""
        if node.lower:
            lower = self.visit(node.lower)
        if node.upper:
            upper = self.visit(node.upper)
        if node.step:
            step = self.visit(node.step)
            return f"({lower}, {upper}, {step})"
        return f"({lower}, {upper})"

    def _visit_container_type(self, typename: Tuple) -> str:
        value_type, index_type = typename
        if isinstance(index_type, List):
            index_contains_default = "Any" in index_type
            if not index_contains_default:
                if isinstance(index_type, List):
                    parsed_items = []
                    for it in index_type:
                        parsed_items.append(it)
                    index_type = ", ".join(parsed_items)
        else:
            index_contains_default = index_type == "Any"
        # Avoid types like HashMap<_, foo>. Prefer default_type instead
        if index_contains_default or value_type == self._default_type:
            return self._default_type
        return self._combine_value_index(value_type, index_type)

    def _get_docstring(self, node) -> str:
        docstring_comment = getattr(node, "docstring_comment", None)
        comment = (
            self.visit_Constant(docstring_comment, quotes=False, docstring=True)
            if docstring_comment
            else None
        )
        return self.comment(comment) if comment else None

    def visit(self, node) -> str:
        if node is None:
            raise AstEmptyNodeFound
        if type(node) in symbols:
            return c_symbol(node)
        else:
            try:
                return super().visit(node)
            except AstNotImplementedError:
                raise
            except Exception as e:
                raise AstNotImplementedError(e, node) from e

    def visit_Module(self, node) -> str:
        # Reset state
        self._usings.clear()
        self._globals.clear()
        self._headers.clear()
        self._imported_names = getattr(node, "imported_names", {})
        self._features.clear()

        # Get attributes
        self._filename = getattr(node, "__file__", None)
        if self._filename:
            self._module = Path(self._filename).stem
        # If there is an import_basedir, use that instead of basedir
        self._basedir = getattr(
            node, "import_basedir", getattr(node, "__basedir__", None)
        )

        self._imports = list(map(get_id, getattr(node, "imports", [])))

        # Visit non-function nodes
        body_dict: Dict[ast.AST, str] = OrderedDict()
        for b in node.body:
            if not isinstance(b, ast.FunctionDef):
                body_dict[b] = self.visit(b)

        # Second pass to handle functiondefs whose body
        # may refer to other members of node.body
        for b in node.body:
            if isinstance(b, ast.FunctionDef):
                body_dict[b] = self.visit(b)

        return self.join_module_body(node, body_dict)

    def join_module_body(self, node, body_dict: Dict[ast.AST, str]):
        """Join the module's body"""
        docstring = self._get_docstring(node)
        buf = [docstring] if docstring is not None else []

        # Append code extras
        features = self.features()
        if features:
            buf.append(features)
        usings = self.usings()
        if usings:
            buf.append(usings)
        globals = self.globals()
        if globals:
            buf.append(globals)

        # Add body contents
        buf.extend([body_dict[b] for b in node.body])

        return "\n".join(buf)

    def visit_ClassDef(self, node):
        bases = [get_id(base) for base in node.bases]
        if set(bases) == {"Enum", "str"}:
            return self.visit_StrEnum(node)
        if len(bases) != 1:
            return None
        if not bases[0] in {"Enum", "IntEnum", "IntFlag"}:
            return None
        if bases == ["IntEnum"] or bases == ["Enum"]:
            return self.visit_IntEnum(node)
        if bases == ["IntFlag"]:
            return self.visit_IntFlag(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        docstring = self._get_docstring(node)
        buf = [docstring] if docstring else []
        buf.extend([self.visit(n) for n in node.body])
        return "\n".join(buf)

    def visit_Pass(self, node) -> str:
        return self.comment("pass")

    def visit_alias(self, node):
        return (node.name, node.asname)

    def _import(self, name: str, alias=None) -> str:
        ...

    def _import_from(self, module_name: str, names: List[str], level: int = 0) -> str:
        ...

    def visit_Import(self, node) -> str:
        names = [self.visit(n) for n in node.names]
        imports = []
        for name, alias in names:
            n_import = name.split(".")
            for i in range(len(n_import)):
                import_name = ".".join(n_import[0 : i + 1])
                if import_name in self._ignored_module_set:
                    break
            else:
                imports.append(self._import(name, alias))
        return "\n".join(imports)

    def visit_ImportFrom(self, node) -> str:
        if node.module in self._ignored_module_set:
            return ""

        imported_name = node.module
        if not node.module:
            # Import from '.'
            imported_name = "."

        names = [self.visit(n) for n in node.names]
        names = [n for n, _ in names]
        return self._import_from(imported_name, names, node.level)

    def visit_Name(self, node) -> str:
        if node.id in self.builtin_constants:
            return node.id.lower()
        return node.id

    def visit_Ellipsis(self, node) -> str:
        return self.comment("...")

    def visit_NameConstant(self, node) -> str:
        if node.value is True:
            return "true"
        elif node.value is False:
            return "false"
        elif node.value is None:
            return "NULL"
        elif node.value is Ellipsis:
            return self.visit_Ellipsis(node)
        else:
            return node.value

    def visit_Constant(self, node) -> str:
        if isinstance(node.value, str):
            return self.visit_Str(node)
        elif isinstance(node.value, bytes):
            return self.visit_Bytes(node)
        return str(self.visit_NameConstant(node))

    def visit_Expr(self, node) -> str:
        s = self.visit(node.value)
        if isinstance(node.value, ast.Constant) and node.value.value is Ellipsis:
            return s
        if not s:
            return ""
        s = s.strip()
        if not s.endswith(self._statement_separator):
            s += self._statement_separator
        if s == self._statement_separator:
            return ""
        else:
            return s

    def visit_Str(self, node) -> str:
        node_str = node.value
        node_str = node_str.replace('"', '\\"')
        node_str = node_str.replace("\n", "\\n")
        node_str = node_str.replace("\r", "\\r")
        node_str = node_str.replace("\t", "\\t")
        return f'"{node_str}"'

    def visit_Bytes(self, node) -> str:
        bytes_str = node.s
        byte_array = ", ".join([hex(c) for c in bytes_str])
        return f"{{{byte_array}}}"

    def visit_arguments(self, node) -> Tuple[List[str], List[str]]:
        args = [self.visit(arg) for arg in node.args]
        if args == []:
            return [], []
        typenames, args = map(list, zip(*args))
        return typenames, args

    def visit_Return(self, node) -> str:
        if node.value:
            return "return {0};".format(self.visit(node.value))
        return "return;"

    def _make_block(self, node):
        buf = []
        buf.append("({")
        buf.extend([self.visit(child) for child in node.body])
        buf.append("})")
        return "\n".join(buf)

    @staticmethod
    def is_block(node):
        # if True: s1; s2  should be transpiled into ({s1; s2;})
        # such that the value of the expression is s2
        # This is a idiom used by rewriters to transform a single ast Node s0
        # into multiple statements s1, s2
        return (
            isinstance(node.test, ast.Constant)
            and node.test.value == True
            and node.orelse == []
            and hasattr(node, "rewritten")
            and node.rewritten
        )

    def visit_If(self, node, use_parens=True) -> str:
        buf = []
        make_block = self.is_block(node)
        if make_block:
            return self._make_block(node)
        else:
            if use_parens:
                buf.append("if({0}) {{".format(self.visit(node.test)))
            else:
                buf.append("if {0} {{".format(self.visit(node.test)))
        body = [self.visit(child) for child in node.body]
        body = [b for b in body if b is not None]
        buf.extend(body)

        orelse = [self.visit(child) for child in node.orelse]
        if orelse:
            buf.append("} else {")
            buf.extend(orelse)
            buf.append("}")
        else:
            buf.append("}")
        return "\n".join(buf)

    def visit_Continue(self, node) -> str:
        return "continue;"

    def visit_Break(self, node) -> str:
        return "break;"

    def visit_While(self, node, use_parens=True) -> str:
        buf = []
        if use_parens:
            buf.append("while ({0}) {{".format(self.visit(node.test)))
        else:
            buf.append("while {0} {{".format(self.visit(node.test)))
        buf.extend([self.visit(n) for n in node.body])
        buf.append("}")
        return "\n".join(buf)

    def visit_Compare(self, node) -> str:
        if isinstance(node.ops[0], ast.In):
            return self.visit_In(node)

        left = self.visit(node.left)
        op = self.visit(node.ops[0])
        right = self.visit(node.comparators[0])

        return "{0} {1} {2}".format(left, op, right)

    def visit_BoolOp(self, node) -> str:
        op = self.visit(node.op)
        return op.join([self.visit(v) for v in node.values])

    def visit_UnaryOp(self, node) -> str:
        return "{0}({1})".format(self.visit(node.op), self.visit(node.operand))

    def _visit_AssignOne(self, node, target) -> str:
        ...

    def visit_Assign(self, node) -> str:
        return "\n".join(
            [self._visit_AssignOne(node, target) for target in node.targets]
        )

    def visit_AugAssign(self, node) -> str:
        target = self.visit(node.target)
        op = self.visit(node.op)
        val = self.visit(node.value)
        return "{0} {1}= {2};".format(target, op, val)

    def visit_AnnAssign(self, node):
        target = self.visit(node.target)
        if (
            hasattr(node.target, "annotation")
            and isinstance(node.target.annotation, ast.Subscript)
            and get_id(node.target.annotation.value) == "Callable"
        ):
            type_str = self._default_type
        else:
            type_str = self._typename_from_annotation(node)
        val = self.visit(node.value) if node.value is not None else None
        return (target, type_str, val)

    def set_continue_on_unimplemented(self):
        self._throw_on_unimplemented = False

    def visit_unsupported_body(self, node, name, body) -> str:
        if self._throw_on_unimplemented:
            raise AstNotImplementedError(f"{name} not implemented", node)
        else:
            return self.comment(
                f"{name} unimplemented on line {node.lineno}:{node.col_offset}"
            )

    def visit_NamedExpr(self, node) -> str:
        target = self.visit(node.target)
        return self.visit_unsupported_body(node, f"named expr {target}", node.value)

    def visit_Delete(self, node) -> str:
        body = [self.visit(t) for t in node.targets]
        return self.visit_unsupported_body(node, "del", body)

    def visit_Await(self, node) -> str:
        return self.visit_unsupported_body(node, "await", node.value)

    def visit_AsyncFor(self, node) -> str:
        target = self.visit(node.target)
        iter = self.visit(node.iter)
        return self.visit_unsupported_body(
            node, f"async for {target} in {iter}", node.body
        )

    def visit_AsyncWith(self, node) -> str:
        items = [self.visit(i) for i in node.items]
        return self.visit_unsupported_body(node, f"async with {items}", node.body)

    def visit_YieldFrom(self, node) -> str:
        return self.visit_unsupported_body(node, "yield from", node.value)

    def visit_AsyncFunctionDef(self, node) -> str:
        return self.visit_unsupported_body(node, "async def", node.body)

    def visit_Nonlocal(self, node) -> str:
        return self.visit_unsupported_body(node, "nonlocal", node.names)

    def visit_DictComp(self, node) -> str:
        key = self.visit(node.key)
        value = self.visit(node.value)
        return self.visit_unsupported_body(
            node, f"dict comprehension ({key}, {value})", node.generators
        )

    def visit_ListComp(self, node) -> str:
        return self.visit_GeneratorExp(node)  # by default, they are the same

    def visit_SetComp(self, node) -> str:
        return self.visit_GeneratorExp(node)  # by default, they are the same

    def visit_StrEnum(self, node) -> str:
        raise Exception("Unimplemented")

    def visit_IntEnum(self, node) -> str:
        raise Exception("Unimplemented")

    def visit_IntFlag(self, node) -> str:
        raise Exception("Unimplemented")

    def visit_IfExp(self, node) -> str:
        body = self.visit(node.body)
        orelse = self.visit(node.orelse)
        test = self.visit(node.test)
        return f"({test}? ({{ {body}; }}) : ({{ {orelse}; }}))"

    ######################################################
    ################### Type Mappings ####################
    ######################################################

    def _map_type(self, typename, lifetime=LifeTime.UNKNOWN) -> str:
        if isinstance(typename, list):
            raise NotImplementedError(f"{typename} not supported in this context")
        typeclass = class_for_typename(typename, self._default_type)
        return self._type_map.get(typeclass, typename)

    def _map_types(self, typenames: List[str]) -> List[str]:
        return [self._map_type(e) for e in typenames]

    def _map_container_type(self, typename) -> str:
        return self._container_type_map.get(typename, self._default_type)

    def _typename_from_type_node(
        self, node, parse_func=None, default=None
    ) -> Union[List, str, None]:
        if isinstance(node, ast.Name):
            return get_id(node)
        elif isinstance(node, ast.Attribute):
            node_id = get_id(node)
            if node_id and node_id.startswith("typing."):
                node_id = node_id.split(".")[1]
            if (mapped_id := self._map_type(node_id)) != node_id:
                return mapped_id
            return f"{self._typename_from_type_node(node.value, parse_func, default)}.{node.attr}"
        elif isinstance(node, ast.Subscript):
            (value_type, index_type) = tuple(
                map(
                    lambda x: self._typename_from_type_node(x, parse_func, default),
                    (node.value, node.slice),
                )
            )
            node.container_type = (value_type, index_type)
            return f"{value_type}[{index_type}]"
        elif isinstance(node, ast.Constant):
            return f"{node.value}"
        elif isinstance(node, ast.Tuple) or isinstance(node, ast.List):
            elts = list(
                map(
                    lambda x: self._typename_from_type_node(x, parse_func, default),
                    node.elts,
                )
            )
            return ", ".join(elts)
        return default

    def _combine_value_index(self, value_type, index_type) -> str:
        return f"{value_type}[{index_type}]"

    def _generic_typename_from_type_node(self, node) -> Union[List, str, None]:
        if isinstance(node, ast.Name):
            return get_id(node)
        elif isinstance(node, ast.Constant):
            if node.value == None:
                return "None"
            return node.value
        elif isinstance(node, ast.ClassDef):
            return get_id(node)
        elif isinstance(node, ast.Tuple):
            type_anns = [self._generic_typename_from_type_node(e) for e in node.elts]
            return f"({', '.join(type_anns)})"
        elif isinstance(node, ast.Attribute):
            node_id = get_id(node)
            if node_id.startswith("typing."):
                node_id = node_id.split(".")[1]
            return node_id
        elif isinstance(node, ast.Subscript):
            slice_value = self._slice_value(node)
            (value_type, index_type) = tuple(
                map(self._generic_typename_from_type_node, (node.value, slice_value))
            )
            node.generic_container_type = (value_type, index_type)
            return f"{value_type}[{index_type}]"
        return self._default_type

    def _typename_from_annotation(self, node, attr="annotation") -> str:
        default_type = self._default_type
        typename = default_type
        if hasattr(node, attr):
            type_node = getattr(node, attr)
            typename = self._typename_from_type_node(type_node)
            if isinstance(type_node, ast.Subscript):
                node.container_type = type_node.container_type
            if (
                isinstance(type_node, ast.Name)
                and (id := get_id(type_node)) in self._container_type_map
            ):
                node.container_type = (id, "Any")

            if cont_type := getattr(node, "container_type", None):
                try:
                    return self._visit_container_type(cont_type)
                except TypeNotSupported as e:
                    raise AstTypeNotSupported(str(e), node)
            if typename is None:
                raise AstCouldNotInfer(type_node, node)
        return typename

    def _generic_typename_from_annotation(
        self, node, attr="annotation"
    ) -> Optional[str]:
        """Unlike the one above, this doesn't do any target specific mapping."""
        typename = None
        if hasattr(node, attr):
            type_node = getattr(node, attr)
            ret = self._generic_typename_from_type_node(type_node)
            if isinstance(type_node, ast.Subscript):
                node.generic_container_type = type_node.generic_container_type
            return ret
        return typename

    def _func_for_lookup(self, fname) -> Union[str, object]:
        func = class_for_typename(fname, None, self._imported_names)
        if func is None:
            return None
        try:
            hash(func)
        except TypeError:
            # Ignore unhashable, probably instance
            logger.debug(f"{func} is not hashable")
            return None
        return func

    def _func_name_split(self, fname: str) -> Tuple[str, str]:
        if not fname:
            return None, None
        splits = fname.rsplit(".", maxsplit=1)
        if len(splits) == 2:
            return tuple(splits)
        else:
            return ("", splits[0])

    def _dispatch(self, node, fname: str, vargs: List[str]) -> Optional[str]:
        if fname in self._dispatch_map:
            try:
                return self._dispatch_map[fname](self, node, vargs)
            except IndexError:
                return None

        if fname in self._small_dispatch_map:
            if fname in self._small_usings_map:
                self._usings.add(self._small_usings_map[fname])
            try:
                return self._small_dispatch_map[fname](node, vargs)
            except IndexError:
                return None

        func = self._func_for_lookup(fname)
        if func is not None and func in self._func_dispatch_table:
            if func in self._func_usings_map:
                self._usings.add(self._func_usings_map[func])
            ret, node.result_type = self._func_dispatch_table[func]
            try:
                return ret(self, node, vargs)
            except IndexError:
                return None

        # string based fallback
        fname_stem, fname_leaf = self._func_name_split(fname)
        if fname_stem and fname_leaf in self._func_dispatch_table:
            ret, node.result_type = self._func_dispatch_table[fname_leaf]
            try:
                return fname_stem + ret(self, node, vargs)
            except IndexError:
                return None
        return None
