# Decorator Names
RESUMABLE = "resumable"
CHANNELS = "channels"
OFFSET_ARRAYS = "offset_arrays"
JL_CLASS = "jl_class"
OOP_CLASS = "oop_class"
PARAMETERIZED = "parameterized"

# Flags
USE_RESUMABLES = "use_resumables"
LOWER_YIELD_FROM = "lower_yield_from"
USE_MODULES = "use_modules"
FIX_SCOPE_BOUNDS = "fix_scope_bounds"
LOOP_SCOPE_WARNING = "loop_scope_warning"
OBJECT_ORIENTED = "oop"
OOP_NESTED_FUNCS = "oop_nested_funcs"
USE_GLOBAL_CONSTANTS = "use_global_constants"
ALLOW_ANNOTATIONS_ON_GLOBALS = "allow_annotations_on_globals"
REMOVE_NESTED_RESUMABLES = "remove_nested_resumables"
OPTIMIZE_LOOP_RANGES = "optimize_loop_ranges"

# Decorators and Flags
REMOVE_NESTED = "remove_nested"

# List holding all global flags
GLOBAL_FLAGS = [
    USE_MODULES,
    USE_RESUMABLES,
    LOWER_YIELD_FROM,
    FIX_SCOPE_BOUNDS,
    LOOP_SCOPE_WARNING,
    OBJECT_ORIENTED,
    OOP_NESTED_FUNCS,
    ALLOW_ANNOTATIONS_ON_GLOBALS,
    USE_GLOBAL_CONSTANTS,
    REMOVE_NESTED_RESUMABLES,
    OPTIMIZE_LOOP_RANGES,
]

FLAG_DEFAULTS = {
    USE_RESUMABLES: False,
    LOWER_YIELD_FROM: False,
    USE_MODULES: True,
    FIX_SCOPE_BOUNDS: False,
    LOOP_SCOPE_WARNING: False,
    OBJECT_ORIENTED: False,
    OOP_NESTED_FUNCS: False,
    USE_GLOBAL_CONSTANTS: False,
    ALLOW_ANNOTATIONS_ON_GLOBALS: False,
    REMOVE_NESTED_RESUMABLES: False,
    OPTIMIZE_LOOP_RANGES: False,
}

###################################
# Julia Types
DEFAULT_TYPE = "Any"
NONE_TYPE = "nothing"

###################################
# Helpers
COMMON_LOOP_VARS = ["v", "w", "x", "y", "z"]
SEP = ["{", "}"]
