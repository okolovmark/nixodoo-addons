import ast
import keyword
import math
import operator
import re

MAX_DATASETS = 8
MAX_MERGES = 4
MAX_FORMULAS = 20
MAX_FORMULA_CHARS = 300
MAX_FORMULA_NODES = 60
MAX_FORMULA_DEPTH = 12
MAX_FUNCTION_ARGS = 10
MAX_GROUPBY = 4
MAX_MEASURES = 20
MAX_FIELDS = 40
# round()'s digits are clamped to this: rounding a whole number to -n digits takes time that grows with n.
MAX_ROUND_DIGITS = 15
# The model's tokenizer is not at hand: a token is taken as two characters of JSON, which overcounts
# the data the model reads rather than undercounting it.
CHARS_PER_TOKEN = 2

SPEC_KEYS = ("datasets", "merges")
DATASET_KEYS = ("name", "model", "domain", "groupby", "measures", "fields", "order", "limit", "formulas")
MERGE_KEYS = ("name", "left", "right", "how", "formulas")
FORMULA_KEYS = ("name", "expression")
MERGE_HOWS = ("inner", "left", "full")
COUNT_MEASURE = "__count"
# A dataset's name is also its xlsx sheet's, which stops at 31 characters.
DATASET_NAME = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
COLUMN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")
GROUPBY_TERM = re.compile(r"^\w+(:\w+)?$")
# "field:function" or "name:function(field)", the function required.
MEASURE_TERM = re.compile(r"^(\w+):(\w+)(?:\((\w+)\))?$")

BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
}
COMPARISONS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}
# The fewest and most arguments of each function.
FUNCTION_ARITY = {
    "abs": (1, 1),
    "round": (1, 2),
    "min": (2, MAX_FUNCTION_ARGS),
    "max": (2, MAX_FUNCTION_ARGS),
    "coalesce": (1, MAX_FUNCTION_ARGS),
}
BINDING_KEYS = ("values", "tables", "charts")
VALUE_BINDING_KEYS = ("id", "dataset", "column", "pick", "match_column", "match_value", "format", "decimals")
TABLE_BINDING_KEYS = ("id", "dataset", "columns", "limit", "total_label")
TABLE_COLUMN_KEYS = ("column", "label", "format", "decimals")
CHART_BINDING_KEYS = ("id", "type", "stacked", "dataset", "label_column", "series")
SERIES_KEYS = ("column", "label")
VALUE_PICKS = ("first", "last", "sum", "avg", "min", "max", "count")
VALUE_FORMATS = ("number", "integer", "percent", "text", "date")
CHART_TYPES = ("bar", "horizontalBar", "line", "pie", "doughnut")
MAX_BINDINGS = {"values": 40, "tables": 10, "charts": 10}
MAX_TABLE_COLUMNS = 20
MAX_SERIES = 8
MAX_DECIMALS = 6
MAX_LABEL_CHARS = 80
ELEMENT_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
FORMULA_GRAMMAR = (
    "numbers, column names, + - * / %, unary minus, parentheses, comparisons (== != < <= > >=) and the"
    " functions abs, round, min, max and coalesce"
)


class ReportSpecError(ValueError):
    """A report specification or formula the AI Assistant does not run."""


def number(value):
    """The value as a formula operand: a number, or None for anything else, booleans included."""
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def finite(value):
    """The value, or None where JSON and a page could not hold it: an infinite float, an int past 2**53."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > 2**53:
        try:
            return finite(float(value))
        except OverflowError:
            return None
    return value


def estimate_tokens(text):
    return len(text) // CHARS_PER_TOKEN + 1


def parse_formula(expression, columns):
    """The checked syntax tree of a formula over `columns`; raises ReportSpecError for anything outside the grammar.

    The tree is only ever walked by `evaluate_formula`, never compiled or
    passed to eval, so a formula can do no more than this check lets through.
    """
    if not isinstance(expression, str) or not expression.strip():
        raise ReportSpecError("A formula's expression is a non-empty string.")
    if len(expression) > MAX_FORMULA_CHARS:
        raise ReportSpecError("A formula is at most %s characters." % MAX_FORMULA_CHARS)
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except (SyntaxError, ValueError) as exc:
        raise ReportSpecError("The formula %r is not an expression of %s." % (expression, FORMULA_GRAMMAR)) from exc
    _check_formula_node(tree.body, columns, 1, [])
    return tree.body


def _check_formula_node(node, columns, depth, seen):
    seen.append(node)
    if len(seen) > MAX_FORMULA_NODES:
        raise ReportSpecError("A formula has at most %s terms." % MAX_FORMULA_NODES)
    if depth > MAX_FORMULA_DEPTH:
        raise ReportSpecError("A formula nests at most %s levels." % MAX_FORMULA_DEPTH)
    if isinstance(node, ast.Constant):
        if type(node.value) not in (int, float):
            raise ReportSpecError("A formula's constants are numbers, not %r." % (node.value,))
        return
    if isinstance(node, ast.Name):
        if node.id not in columns:
            raise ReportSpecError(
                "A formula uses %s, which is not one of the columns before it: %s." % (node.id, ", ".join(columns))
            )
        return
    if isinstance(node, ast.BinOp) and type(node.op) in BINARY_OPERATORS:
        children = (node.left, node.right)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        children = (node.operand,)
    elif isinstance(node, ast.Compare) and all(type(op) in COMPARISONS for op in node.ops):
        children = (node.left, *node.comparators)
    elif isinstance(node, ast.Call):
        _check_formula_call(node)
        children = node.args
    else:
        construct = type(getattr(node, "op", node)).__name__
        raise ReportSpecError("A formula has no %s: only %s." % (construct, FORMULA_GRAMMAR))
    for child in children:
        _check_formula_node(child, columns, depth + 1, seen)


def _check_formula_call(node):
    if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTION_ARITY:
        raise ReportSpecError("A formula calls only %s." % ", ".join(FUNCTION_ARITY))
    if node.keywords or any(isinstance(argument, ast.Starred) for argument in node.args):
        raise ReportSpecError("A formula passes a function its arguments plainly, as in %s(a, b)." % node.func.id)
    fewest, most = FUNCTION_ARITY[node.func.id]
    if not fewest <= len(node.args) <= most:
        raise ReportSpecError("%s takes %s to %s arguments." % (node.func.id, fewest, most))


def evaluate_formula(node, row):
    """A parsed formula's value on one row: a number, a boolean for a comparison, or None.

    A column without a number is None, and so is every operation on a None
    but coalesce, a division or modulo by zero, and an overflow.
    """
    try:
        return finite(_evaluate(node, row))
    except (OverflowError, ValueError):
        return None


def _evaluate(node, row):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return number(row.get(node.id))
    if isinstance(node, ast.UnaryOp):
        operand = number(_evaluate(node.operand, row))
        return None if operand is None else -operand
    if isinstance(node, ast.BinOp):
        left, right = number(_evaluate(node.left, row)), number(_evaluate(node.right, row))
        if left is None or right is None:
            return None
        if isinstance(node.op, (ast.Div, ast.Mod)) and right == 0:
            return None
        return finite(BINARY_OPERATORS[type(node.op)](left, right))
    if isinstance(node, ast.Compare):
        values = [number(_evaluate(child, row)) for child in (node.left, *node.comparators)]
        if any(value is None for value in values):
            return None
        return all(COMPARISONS[type(op)](a, b) for op, a, b in zip(node.ops, values, values[1:]))
    arguments = [_evaluate(argument, row) for argument in node.args]
    if node.func.id == "coalesce":
        return next((value for value in arguments if value is not None), None)
    numbers = [number(value) for value in arguments]
    if any(value is None for value in numbers):
        return None
    if node.func.id == "round":
        if len(numbers) == 1:
            return round(numbers[0])
        return round(numbers[0], max(-MAX_ROUND_DIGITS, min(MAX_ROUND_DIGITS, int(numbers[1]))))
    return {"abs": abs, "min": min, "max": max}[node.func.id](*numbers)


def apply_formulas(rows, formulas):
    """The rows with each formula's column added, in order: a formula sees the columns before it."""
    for row in rows:
        for name, tree in formulas:
            row[name] = evaluate_formula(tree, row)
    return rows


def merge_key(row, keys, unmatched=()):
    """The row's group key, a many2one ([id, name]) by its id; a key that matches no other when a part of it is
    one of the `unmatched` values, such as a masked link."""
    parts = tuple(_key_part(row.get(key)) for key in keys)
    if any(isinstance(part, str) and part in unmatched for part in parts):
        return object()
    return parts


def _key_part(value):
    return value[0] if isinstance(value, list) and value else value


def merge_rows(merge, left_rows, right_rows, unmatched=()):
    """The rows of a checked merge: left's rows joined to right's on the group keys, as its `how` says.

    inner keeps the keys on both sides, left every left row, full every key
    of either side; a side without the key leaves its columns None. A key
    with one of the `unmatched` values matches no other: rows whose link was
    masked may be different records.
    """
    right_by_key = {}
    for row in right_rows:
        right_by_key.setdefault(merge_key(row, merge["right_keys"], unmatched), row)
    left_columns = [*merge["left_keys"], *merge["left_values"]]
    rows, left_keys_seen = [], set()
    for row in left_rows:
        key = merge_key(row, merge["left_keys"], unmatched)
        right_row = right_by_key.get(key)
        if right_row is None and merge["how"] == "inner":
            continue
        left_keys_seen.add(key)
        rows.append(
            {
                **{column: row.get(column) for column in left_columns},
                **{column: (right_row or {}).get(column) for column in merge["right_values"]},
            }
        )
    if merge["how"] == "full":
        for key, right_row in right_by_key.items():
            if key in left_keys_seen:
                continue
            rows.append(
                {
                    **{left: right_row.get(right) for left, right in zip(merge["left_keys"], merge["right_keys"])},
                    **dict.fromkeys(merge["left_values"]),
                    **{column: right_row.get(column) for column in merge["right_values"]},
                }
            )
    return rows


def validate_spec(spec):
    """The specification checked for its shape and limits: (datasets, merges), each with its columns and parsed formulas.

    Models, fields, rights and the AI deny list are checked when the queries run.
    """
    _check_keys(spec, SPEC_KEYS, "The specification")
    datasets, merges = spec["datasets"], spec["merges"]
    if not isinstance(datasets, list) or not 1 <= len(datasets) <= MAX_DATASETS:
        raise ReportSpecError("A specification has 1 to %s datasets." % MAX_DATASETS)
    if not isinstance(merges, list) or len(merges) > MAX_MERGES:
        raise ReportSpecError("A specification has at most %s merges." % MAX_MERGES)
    tables = {}
    checked_datasets = [_check_dataset(dataset, tables) for dataset in datasets]
    checked_merges = [_check_merge(merge, tables) for merge in merges]
    return checked_datasets, checked_merges


def _check_keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        found = ", ".join(sorted(value)) if isinstance(value, dict) else type(value).__name__
        raise ReportSpecError("%s is an object with the keys %s, not %s." % (label, ", ".join(keys), found or "none"))


def _check_strings(value, label, most):
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value) or len(value) > most:
        raise ReportSpecError("%s is a list of at most %s strings." % (label, most))
    return value


def _check_string(value, label):
    if not isinstance(value, str):
        raise ReportSpecError("%s is a string." % label)
    return value


def _new_name(name, tables):
    if not isinstance(name, str) or not DATASET_NAME.match(name):
        raise ReportSpecError(
            "A dataset or merge is named with lowercase letters, digits and _, starting with a letter, at most"
            " 31 characters, not %r." % (name,)
        )
    if name in tables:
        raise ReportSpecError("Two datasets or merges are named %s." % name)
    return name


def _check_formulas(formulas, columns, label):
    """[(name, parsed tree)] of a dataset's or merge's formulas; each sees the columns and formulas before it."""
    if not isinstance(formulas, list) or len(formulas) > MAX_FORMULAS:
        raise ReportSpecError("%s has a list of at most %s formulas." % (label, MAX_FORMULAS))
    known_columns, parsed = list(columns), []
    for formula in formulas:
        _check_keys(formula, FORMULA_KEYS, "A formula of %s" % label)
        name = formula["name"]
        if not isinstance(name, str) or not COLUMN_NAME.match(name) or keyword.iskeyword(name):
            raise ReportSpecError(
                "A formula is named with letters, digits and _, at most 40 characters, not %r." % (name,)
            )
        if name in known_columns:
            raise ReportSpecError("%s already has a column %s." % (label, name))
        parsed.append((name, parse_formula(formula["expression"], known_columns)))
        known_columns.append(name)
    return parsed


def _check_dataset(dataset, tables):
    _check_keys(dataset, DATASET_KEYS, "A dataset")
    name = _new_name(dataset["name"], tables)
    label = "The dataset %s" % name
    model = _check_string(dataset["model"], "%s's model" % label)
    _check_string(dataset["domain"], "%s's domain" % label)
    _check_string(dataset["order"], "%s's order" % label)
    groupby = _check_strings(dataset["groupby"], "%s's groupby" % label, MAX_GROUPBY)
    measures = _check_strings(dataset["measures"], "%s's measures" % label, MAX_MEASURES)
    field_names = _check_strings(dataset["fields"], "%s's fields" % label, MAX_FIELDS)
    limit = dataset["limit"]
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise ReportSpecError("%s's limit is a whole number, 0 for every row." % label)
    if not model:
        raise ReportSpecError("%s names its model." % label)
    if field_names and (groupby or measures):
        raise ReportSpecError(
            "%s lists either fields, for a list of records, or groupby and measures, for a grouped dataset;"
            " not both." % label
        )
    if field_names:
        bad_names = [field_name for field_name in field_names if not COLUMN_NAME.match(field_name)]
        if bad_names:
            raise ReportSpecError("%s lists field names, not %s." % (label, ", ".join(map(repr, bad_names))))
        columns, keys = list(dict.fromkeys(["id", "display_name", *field_names])), []
    elif measures or groupby:
        bad_terms = [term for term in groupby if not GROUPBY_TERM.match(term)]
        if bad_terms:
            raise ReportSpecError(
                '%s groups by "field" or "field:granularity", not %s.' % (label, ", ".join(map(repr, bad_terms)))
            )
        outputs = []
        for measure in measures:
            match = MEASURE_TERM.match(measure)
            if measure != COUNT_MEASURE and not match:
                raise ReportSpecError(
                    '%s\'s measures are "__count", "field:function" or "name:function(field)", not %r.'
                    % (label, measure)
                )
            outputs.append(match.group(1) if match else COUNT_MEASURE)
        columns, keys = [*groupby, *outputs], list(groupby)
        if len(set(columns)) != len(columns):
            raise ReportSpecError("%s names a column twice: give a measure its own name with an alias." % label)
    else:
        raise ReportSpecError(
            "%s needs fields, for a list of records, or groupby and measures, for a grouped dataset." % label
        )
    formulas = _check_formulas(dataset["formulas"], columns, label)
    tables[name] = {"columns": [*columns, *(formula_name for formula_name, _tree in formulas)], "keys": keys}
    return {
        "name": name,
        "model": model,
        "domain": dataset["domain"],
        "groupby": groupby,
        "measures": measures,
        "fields": field_names,
        "order": dataset["order"],
        "limit": limit,
        "is_grouped": not field_names,
        "base_columns": columns,
        "columns": tables[name]["columns"],
        "keys": keys,
        "formulas": formulas,
    }


def _check_merge(merge, tables):
    _check_keys(merge, MERGE_KEYS, "A merge")
    name = _new_name(merge["name"], tables)
    label = "The merge %s" % name
    for side in ("left", "right"):
        source = merge[side]
        if not isinstance(source, str) or source not in tables:
            raise ReportSpecError("%s merges %r, which is not a dataset or merge before it." % (label, source))
        if not tables[source]["keys"]:
            raise ReportSpecError(
                "%s merges %s, which has no group keys: only grouped datasets merge." % (label, source)
            )
    left, right = merge["left"], merge["right"]
    if left == right:
        raise ReportSpecError("%s merges two different datasets." % label)
    if merge["how"] not in MERGE_HOWS:
        raise ReportSpecError("%s's how is one of %s, not %r." % (label, ", ".join(MERGE_HOWS), merge["how"]))
    left_keys, right_keys = tables[left]["keys"], tables[right]["keys"]
    if len(left_keys) != len(right_keys):
        raise ReportSpecError(
            "%s merges datasets grouped by as many keys: %s has %s, %s has %s."
            % (label, left, len(left_keys), right, len(right_keys))
        )
    left_values = [column for column in tables[left]["columns"] if column not in left_keys]
    right_values = [column for column in tables[right]["columns"] if column not in right_keys]
    clashes = [column for column in right_values if column in left_keys or column in left_values]
    if clashes:
        raise ReportSpecError(
            '%s has the column %s on both sides: name one with an alias, as in "revenue:sum(price_subtotal)".'
            % (label, ", ".join(clashes))
        )
    columns = [*left_keys, *left_values, *right_values]
    formulas = _check_formulas(merge["formulas"], columns, label)
    tables[name] = {"columns": [*columns, *(formula_name for formula_name, _tree in formulas)], "keys": list(left_keys)}
    return {
        "name": name,
        "left": left,
        "right": right,
        "how": merge["how"],
        "left_keys": list(left_keys),
        "right_keys": list(right_keys),
        "left_values": left_values,
        "right_values": right_values,
        "columns": tables[name]["columns"],
        "formulas": formulas,
    }


def validate_bindings(bindings, columns_by_dataset, element_ids):
    """Check the page's bindings: their shape and limits, the datasets and columns they read, and that each fills
    an element of the page, one binding an element."""
    _check_keys(bindings, BINDING_KEYS, "The bindings")
    checks = {"values": _check_value_binding, "tables": _check_table_binding, "charts": _check_chart_binding}
    bound_ids = set()
    for kind, check in checks.items():
        kind_bindings = bindings[kind]
        if not isinstance(kind_bindings, list) or len(kind_bindings) > MAX_BINDINGS[kind]:
            raise ReportSpecError("The bindings' %s are a list of at most %s." % (kind, MAX_BINDINGS[kind]))
        for binding in kind_bindings:
            check(binding, columns_by_dataset)
            element_id = binding["id"]
            if not isinstance(element_id, str) or not ELEMENT_ID.match(element_id):
                raise ReportSpecError(
                    "A binding's id is letters, digits, _ and -, starting with a letter, not %r." % (element_id,)
                )
            if element_id in bound_ids:
                raise ReportSpecError("Two bindings fill the element %s." % element_id)
            if element_id not in element_ids:
                raise ReportSpecError(
                    'The binding %s fills an element the page does not have: add one with id="%s".'
                    % (element_id, element_id)
                )
            bound_ids.add(element_id)


def _binding_columns(binding, columns_by_dataset):
    dataset = binding["dataset"]
    if not isinstance(dataset, str) or dataset not in columns_by_dataset:
        raise ReportSpecError(
            "The binding %s reads %r, which is not a dataset or merge of the specification: %s."
            % (binding["id"], dataset, ", ".join(columns_by_dataset))
        )
    return columns_by_dataset[dataset]


def _check_binding_column(binding, column, columns):
    if column not in columns:
        raise ReportSpecError(
            "The binding %s reads the column %r, which %s does not have: %s."
            % (binding["id"], column, binding["dataset"], ", ".join(columns))
        )


def _check_format(binding, value_format, decimals):
    if value_format not in VALUE_FORMATS:
        raise ReportSpecError("The binding %s formats as one of %s." % (binding["id"], ", ".join(VALUE_FORMATS)))
    if not isinstance(decimals, int) or isinstance(decimals, bool) or not 0 <= decimals <= MAX_DECIMALS:
        raise ReportSpecError("The binding %s has 0 to %s decimals." % (binding["id"], MAX_DECIMALS))


def _check_label(binding, label):
    if not isinstance(label, str) or len(label) > MAX_LABEL_CHARS:
        raise ReportSpecError("The binding %s has labels of at most %s characters." % (binding["id"], MAX_LABEL_CHARS))


def _check_value_binding(binding, columns_by_dataset):
    _check_keys(binding, VALUE_BINDING_KEYS, "A value binding")
    columns = _binding_columns(binding, columns_by_dataset)
    _check_binding_column(binding, binding["column"], columns)
    if binding["pick"] not in VALUE_PICKS:
        raise ReportSpecError("The binding %s picks one of %s." % (binding["id"], ", ".join(VALUE_PICKS)))
    if binding["match_column"] != "":
        _check_binding_column(binding, binding["match_column"], columns)
    _check_string(binding["match_value"], "The match_value of %s" % binding["id"])
    _check_format(binding, binding["format"], binding["decimals"])


def _check_table_binding(binding, columns_by_dataset):
    _check_keys(binding, TABLE_BINDING_KEYS, "A table binding")
    columns = _binding_columns(binding, columns_by_dataset)
    table_columns = binding["columns"]
    if not isinstance(table_columns, list) or not 1 <= len(table_columns) <= MAX_TABLE_COLUMNS:
        raise ReportSpecError("The table %s shows 1 to %s columns." % (binding["id"], MAX_TABLE_COLUMNS))
    for table_column in table_columns:
        _check_keys(table_column, TABLE_COLUMN_KEYS, "A column of the table %s" % binding["id"])
        _check_binding_column(binding, table_column["column"], columns)
        _check_label(binding, table_column["label"])
        _check_format(binding, table_column["format"], table_column["decimals"])
    limit = binding["limit"]
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise ReportSpecError("The table %s's limit is a whole number, 0 for every row." % binding["id"])
    _check_label(binding, binding["total_label"])


def _check_chart_binding(binding, columns_by_dataset):
    _check_keys(binding, CHART_BINDING_KEYS, "A chart binding")
    columns = _binding_columns(binding, columns_by_dataset)
    if binding["type"] not in CHART_TYPES:
        raise ReportSpecError("The chart %s is one of %s." % (binding["id"], ", ".join(CHART_TYPES)))
    if not isinstance(binding["stacked"], bool):
        raise ReportSpecError("The chart %s's stacked is true or false." % binding["id"])
    _check_binding_column(binding, binding["label_column"], columns)
    series = binding["series"]
    if not isinstance(series, list) or not 1 <= len(series) <= MAX_SERIES:
        raise ReportSpecError("The chart %s has 1 to %s series." % (binding["id"], MAX_SERIES))
    for one_series in series:
        _check_keys(one_series, SERIES_KEYS, "A series of the chart %s" % binding["id"])
        _check_binding_column(binding, one_series["column"], columns)
        _check_label(binding, one_series["label"])


def spec_columns(spec):
    """The columns of each dataset and merge of a specification, by name."""
    datasets, merges = validate_spec(spec)
    return {table["name"]: table["columns"] for table in (*datasets, *merges)}
