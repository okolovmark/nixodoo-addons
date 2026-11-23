import ast
import copy
import time

from odoo.tests import tagged
from odoo.tests.common import BaseCase

from odoo.addons.ai_assistant.utils.report_spec import (
    MAX_DATASETS,
    MAX_FIELDS,
    MAX_FORMULA_CHARS,
    MAX_FORMULA_DEPTH,
    MAX_FORMULA_NODES,
    MAX_FORMULAS,
    MAX_GROUPBY,
    MAX_MEASURES,
    MAX_MERGES,
    ReportSpecError,
    _binding_columns,
    _check_binding_column,
    _check_chart_binding,
    _check_dataset,
    _check_format,
    _check_formula_call,
    _check_formula_node,
    _check_formulas,
    _check_keys,
    _check_label,
    _check_merge,
    _check_string,
    _check_strings,
    _check_table_binding,
    _check_value_binding,
    _evaluate,
    _key_part,
    _new_name,
    apply_formulas,
    estimate_tokens,
    evaluate_formula,
    finite,
    merge_key,
    merge_rows,
    number,
    parse_formula,
    spec_columns,
    validate_bindings,
    validate_spec,
)

COLUMNS = ["revenue", "cost", "orders", "label"]


def dataset(name="sales", **values):
    return {
        "name": name,
        "model": "sale.order",
        "domain": "[]",
        "groupby": ["partner_id"],
        "measures": ["revenue:sum(amount_untaxed)"],
        "fields": [],
        "order": "",
        "limit": 0,
        "formulas": [],
        **values,
    }


def merge(name="joined", left="sales", right="costs", how="left", formulas=()):
    return {"name": name, "left": left, "right": right, "how": how, "formulas": list(formulas)}


def spec(*datasets, merges=()):
    return {"datasets": list(datasets) or [dataset()], "merges": list(merges)}


@tagged("post_install", "-at_install")
class TestReportFormula(BaseCase):
    def _value(self, expression, **row):
        return evaluate_formula(parse_formula(expression, COLUMNS), row)

    def test_evaluate_formula_computes_the_allowed_grammar(self):
        row = {"revenue": 200, "cost": 150, "orders": 4, "label": "Acme"}
        cases = {
            "(revenue - cost) / revenue * 100": 25.0,
            "-revenue + 1": -199,
            "revenue % 3": 2,
            "revenue > cost": True,
            "cost <= revenue < 1000": True,
            "revenue == cost": False,
            "revenue != cost": True,
            "abs(cost - revenue)": 50,
            "round(cost / orders)": 38,
            "round(cost / 7, 2)": 21.43,
            "min(revenue, cost, 160)": 150,
            "max(revenue, cost)": 200,
            "coalesce(label, cost)": 150,
            "1.5e2": 150.0,
        }
        for expression, expected in cases.items():
            with self.subTest(expression):
                self.assertEqual(self._value(expression, **row), expected)

    def test_a_null_spreads_and_a_division_by_zero_is_null(self):
        cases = {
            "revenue / cost": {"revenue": 10, "cost": 0},
            "revenue % cost": {"revenue": 10, "cost": 0},
            "revenue / orders": {"revenue": 10},
            "revenue + label": {"revenue": 10, "label": "text"},
            "revenue * 2": {"revenue": False},
            "revenue * 2 ": {"revenue": True},
            "-label": {"label": [3, "Acme"]},
            "revenue > cost": {"revenue": 1, "cost": None},
            "abs(cost)": {},
            "min(revenue, cost)": {"revenue": 1},
            "round(revenue, cost)": {"revenue": 1.5},
            "(revenue > 1) + 1": {"revenue": 5},
        }
        for expression, row in cases.items():
            with self.subTest(expression):
                self.assertIsNone(self._value(expression, **row))
        self.assertEqual(self._value("coalesce(cost, revenue, 0)", revenue=None), 0)
        self.assertIsNone(self._value("coalesce(cost, revenue)"))

    def test_overflow_and_infinity_are_null(self):
        self.assertIsNone(self._value("revenue * 1e308 * 10", revenue=10))
        self.assertIsNone(self._value("1e999"))
        self.assertIsNone(self._value("round(revenue, cost)", revenue=1.5, cost=1e308 * 10))
        self.assertIsNone(self._value("revenue * 1.0", revenue=10**400))
        self.assertEqual(self._value("revenue * revenue", revenue=2**30), float(2**60))
        self.assertEqual(finite(2**53), 2**53)
        self.assertIsNone(finite(float("nan")))
        self.assertIsNone(finite(10**400))

    def test_round_digits_are_clamped_so_a_huge_one_returns_at_once(self):
        started = time.time()
        count_formula = parse_formula("round(__count, -1e18)", ["__count"])
        self.assertEqual(evaluate_formula(count_formula, {"__count": 123456}), 0)
        self.assertEqual(self._value("round(revenue, 1e18)", revenue=1.25), 1.25)
        self.assertLess(time.time() - started, 1, "round() never works through billions of digits")
        self.assertEqual(self._value("round(revenue, -2)", revenue=1234), 1200)

    def test_parse_formula_refuses_everything_outside_the_grammar(self):
        refused = (
            "revenue.real",
            "__import__('os')",
            "open('/etc/passwd')",
            "revenue ** 2",
            "revenue // 2",
            "revenue & 1",
            "not revenue",
            "revenue and cost",
            "revenue if cost else 0",
            "[revenue]",
            "(revenue, cost)",
            "{'a': revenue}",
            "lambda: revenue",
            "[x for x in (1,)]",
            "revenue[0]",
            "'text'",
            "True",
            "None",
            "revenue in (1, 2)",
            "revenue is cost",
            "+revenue",
            "abs(x=revenue)",
            "max(*COLUMNS)",
            "abs(revenue, cost)",
            "min(revenue)",
            "round()",
            "sorted(revenue)",
            "revenue.__class__",
            "(lambda: 1)()",
            "unknown_column + 1",
            "revenue +",
            "",
            "   ",
            "revenue = 1",
            "f'{revenue}'",
            "revenue\x00",
        )
        for expression in refused:
            with self.subTest(expression), self.assertRaises(ReportSpecError):
                parse_formula(expression, COLUMNS)
        with self.assertRaises(ReportSpecError):
            parse_formula(42, COLUMNS)

    def test_parse_formula_limits_size_nodes_and_depth(self):
        with self.assertRaisesRegex(ReportSpecError, "at most %s characters" % MAX_FORMULA_CHARS):
            parse_formula("1" + " + 1" * MAX_FORMULA_CHARS, COLUMNS)
        ten_ones = "max(%s)" % ",".join(["1"] * 10)
        with self.assertRaisesRegex(ReportSpecError, "at most %s terms" % MAX_FORMULA_NODES):
            parse_formula("max(%s)" % ",".join([ten_ones] * 10), COLUMNS)
        with self.assertRaisesRegex(ReportSpecError, "nests at most %s levels" % MAX_FORMULA_DEPTH):
            parse_formula("-" * MAX_FORMULA_DEPTH + "revenue", COLUMNS)
        self.assertIsInstance(parse_formula("-" * (MAX_FORMULA_DEPTH - 1) + "revenue", COLUMNS), ast.UnaryOp)

    def test_check_formula_node_and_call_directly(self):
        self.assertEqual(_evaluate(ast.parse("revenue + 2", mode="eval").body, {"revenue": 1}), 3)
        call = ast.parse("max(revenue, cost)", mode="eval").body
        _check_formula_call(call)
        _check_formula_node(call, COLUMNS, 1, [])
        with self.assertRaisesRegex(ReportSpecError, "calls only"):
            _check_formula_call(ast.parse("len(revenue)", mode="eval").body)
        with self.assertRaisesRegex(ReportSpecError, "not one of the columns"):
            _check_formula_node(ast.parse("margin", mode="eval").body, COLUMNS, 1, [])

    def test_apply_formulas_sees_the_earlier_formulas(self):
        rows = [{"revenue": 200, "cost": 150}, {"revenue": 0, "cost": 10}]
        margin = parse_formula("revenue - cost", ["revenue", "cost"])
        margin_pct = parse_formula("margin / revenue * 100", ["revenue", "cost", "margin"])
        self.assertEqual(
            apply_formulas(rows, [("margin", margin), ("margin_pct", margin_pct)]),
            [
                {"revenue": 200, "cost": 150, "margin": 50, "margin_pct": 25.0},
                {"revenue": 0, "cost": 10, "margin": -10, "margin_pct": None},
            ],
        )

    def test_number_and_estimate_tokens(self):
        self.assertEqual([number(value) for value in (3, 2.5, True, False, "4", None, [1])], [3, 2.5] + [None] * 5)
        self.assertEqual(estimate_tokens(""), 1)
        self.assertEqual(estimate_tokens("x" * 300), 151, "two characters a token, to overcount rather than under")


@tagged("post_install", "-at_install")
class TestReportSpec(BaseCase):
    def test_validate_spec_returns_columns_keys_and_parsed_formulas(self):
        rows_dataset = dataset(
            "orders",
            groupby=[],
            measures=[],
            fields=["name", "amount_total", "name"],
            formulas=[{"name": "half", "expression": "amount_total / 2"}],
        )
        datasets, merges = validate_spec(
            spec(
                dataset(
                    groupby=["partner_id", "date_order:month"],
                    measures=["revenue:sum(amount_untaxed)", "amount_total:avg", "__count"],
                    formulas=[{"name": "per_order", "expression": "revenue / __count"}],
                ),
                rows_dataset,
            )
        )
        sales, orders = datasets
        self.assertEqual(merges, [])
        self.assertEqual(
            (sales["is_grouped"], sales["keys"], sales["base_columns"], sales["columns"]),
            (
                True,
                ["partner_id", "date_order:month"],
                ["partner_id", "date_order:month", "revenue", "amount_total", "__count"],
                ["partner_id", "date_order:month", "revenue", "amount_total", "__count", "per_order"],
            ),
        )
        self.assertEqual([name for name, _tree in sales["formulas"]], ["per_order"])
        self.assertEqual(
            (orders["is_grouped"], orders["keys"], orders["columns"]),
            (False, [], ["id", "display_name", "name", "amount_total", "half"]),
        )
        total, _merges = validate_spec(spec(dataset("total", groupby=[], measures=["__count"])))
        self.assertEqual((total[0]["is_grouped"], total[0]["keys"]), (True, []))

    def test_validate_spec_refuses_unknown_keys_and_wrong_types(self):
        cases = {
            "no object": [],
            "extra key": {"datasets": [dataset()], "merges": [], "page": ""},
            "missing key": {"datasets": [dataset()]},
            "no dataset": {"datasets": [], "merges": []},
            "too many datasets": {
                "datasets": [dataset(f"d{index}") for index in range(MAX_DATASETS + 1)],
                "merges": [],
            },
            "merges not a list": {"datasets": [dataset()], "merges": {}},
            "dataset extra key": spec({**dataset(), "sql": "SELECT 1"}),
            "dataset missing key": spec({key: value for key, value in dataset().items() if key != "limit"}),
            "bad name": spec(dataset("Sales")),
            "long name": spec(dataset("s" * 32)),
            "duplicate name": spec(dataset(), dataset()),
            "model not a string": spec(dataset(model=["sale.order"])),
            "empty model": spec(dataset(model="")),
            "domain not a string": spec(dataset(domain=[])),
            "order not a string": spec(dataset(order=None)),
            "groupby not strings": spec(dataset(groupby=[1])),
            "too many group keys": spec(dataset(groupby=[f"g{index}" for index in range(MAX_GROUPBY + 1)])),
            "too many measures": spec(dataset(measures=[f"m{index}:sum" for index in range(MAX_MEASURES + 1)])),
            "too many fields": spec(
                dataset(groupby=[], measures=[], fields=[f"f{index}" for index in range(MAX_FIELDS + 1)])
            ),
            "negative limit": spec(dataset(limit=-1)),
            "boolean limit": spec(dataset(limit=True)),
            "float limit": spec(dataset(limit=1.5)),
            "fields and measures": spec(dataset(fields=["name"])),
            "nothing to read": spec(dataset(groupby=[], measures=[])),
            "bad field name": spec(dataset(groupby=[], measures=[], fields=["partner_id.name"])),
            "bad groupby": spec(dataset(groupby=["partner_id.country_id"])),
            "measure without function": spec(dataset(measures=["amount_total"])),
            "measure with sql": spec(dataset(measures=["x:sum(amount) FROM res_users --"])),
            "column named twice": spec(dataset(measures=["partner_id:count"])),
            "formulas not a list": spec(dataset(formulas={})),
            "too many formulas": spec(
                dataset(formulas=[{"name": f"f{index}", "expression": "1"} for index in range(MAX_FORMULAS + 1)])
            ),
            "formula extra key": spec(dataset(formulas=[{"name": "x", "expression": "1", "code": ""}])),
            "formula keyword name": spec(dataset(formulas=[{"name": "lambda", "expression": "1"}])),
            "formula bad name": spec(dataset(formulas=[{"name": "a-b", "expression": "1"}])),
            "formula shadows a column": spec(dataset(formulas=[{"name": "revenue", "expression": "1"}])),
            "formula uses a later formula": spec(
                dataset(
                    formulas=[{"name": "a", "expression": "b + 1"}, {"name": "b", "expression": "1"}],
                )
            ),
        }
        for case, value in cases.items():
            with self.subTest(case), self.assertRaises(ReportSpecError):
                validate_spec(value)

    def test_validate_spec_checks_merges(self):
        sales = dataset(formulas=[{"name": "double", "expression": "revenue * 2"}])
        costs = dataset("costs", model="purchase.order", measures=["cost:sum(amount_untaxed)"])
        listed = dataset("listed", groupby=[], measures=[], fields=["name"])
        by_two = dataset("by_two", groupby=["partner_id", "user_id"], measures=["__count"])
        clashing = dataset("clashing", measures=["revenue:sum(amount_total)"])
        _datasets, [joined, again] = validate_spec(
            spec(
                sales,
                costs,
                dataset("third", measures=["third:count"]),
                merges=[
                    merge(formulas=[{"name": "margin", "expression": "revenue - cost"}]),
                    merge("again", left="joined", right="third", how="full"),
                ],
            )
        )
        self.assertEqual(
            (joined["left_keys"], joined["right_keys"], joined["left_values"], joined["right_values"]),
            (["partner_id"], ["partner_id"], ["revenue", "double"], ["cost"]),
        )
        self.assertEqual(joined["columns"], ["partner_id", "revenue", "double", "cost", "margin"])
        self.assertEqual(again["columns"], ["partner_id", "revenue", "double", "cost", "margin", "third"])
        cases = {
            "unknown side": spec(sales, merges=[merge(right="nowhere")]),
            "a later dataset": spec(sales, merges=[merge("first", right="joined"), merge()]),
            "a row list": spec(sales, listed, merges=[merge(right="listed")]),
            "itself": spec(sales, merges=[merge(right="sales")]),
            "unknown how": spec(sales, costs, merges=[merge(how="cross")]),
            "keys of another count": spec(sales, by_two, merges=[merge(right="by_two")]),
            "a shared column": spec(sales, clashing, merges=[merge(right="clashing")]),
            "a name taken": spec(sales, costs, merges=[merge(name="sales")]),
            "too many merges": spec(sales, costs, merges=[merge(f"m{index}") for index in range(MAX_MERGES + 1)]),
            "merge extra key": spec(sales, costs, merges=[{**merge(), "on": "partner_id"}]),
            "merge formula": spec(sales, costs, merges=[merge(formulas=[{"name": "x", "expression": "nope"}])]),
        }
        for case, value in cases.items():
            with self.subTest(case), self.assertRaises(ReportSpecError):
                validate_spec(value)

    def test_validate_spec_leaves_the_spec_unchanged(self):
        original = spec(dataset(formulas=[{"name": "double", "expression": "revenue * 2"}]))
        snapshot = copy.deepcopy(original)
        validate_spec(original)
        self.assertEqual(original, snapshot)

    def test_shape_helpers(self):
        _check_keys({"a": 1}, ("a",), "It")
        with self.assertRaisesRegex(ReportSpecError, "It is an object with the keys a, not b"):
            _check_keys({"b": 1}, ("a",), "It")
        self.assertEqual(_check_strings(["x"], "It", 1), ["x"])
        with self.assertRaises(ReportSpecError):
            _check_strings("x", "It", 1)
        with self.assertRaisesRegex(ReportSpecError, "It is a list of at most 1 strings"):
            _check_strings(["x", "y"], "It", 1)
        self.assertEqual(_check_string("x", "It"), "x")
        with self.assertRaises(ReportSpecError):
            _check_string(1, "It")
        self.assertEqual(_new_name("sales_2026", {}), "sales_2026")
        with self.assertRaises(ReportSpecError):
            _new_name("sales", {"sales": {}})

    def test_check_dataset_merge_and_formulas_fill_the_tables(self):
        tables = {}
        self.assertEqual(_check_dataset(dataset(), tables)["columns"], ["partner_id", "revenue"])
        costs = dataset("costs", measures=["cost:sum(amount_untaxed)"])
        self.assertEqual(_check_dataset(costs, tables)["keys"], ["partner_id"])
        self.assertEqual(_check_merge(merge(), tables)["columns"], ["partner_id", "revenue", "cost"])
        self.assertEqual(set(tables), {"sales", "costs", "joined"})
        formulas = _check_formulas([{"name": "double", "expression": "revenue * 2"}], ["revenue"], "It")
        self.assertEqual([name for name, _tree in formulas], ["double"])


@tagged("post_install", "-at_install")
class TestReportMerge(BaseCase):
    def _merge(self, how):
        _datasets, [checked_merge] = validate_spec(
            spec(
                dataset(),
                dataset("costs", model="purchase.order", measures=["cost:sum(amount_untaxed)"]),
                merges=[merge(how=how)],
            )
        )
        return checked_merge

    def test_merge_rows_joins_on_the_many2one_id(self):
        sales = [
            {"partner_id": [1, "Acme"], "revenue": 100},
            {"partner_id": [2, "Beta"], "revenue": 50},
            {"partner_id": False, "revenue": 5},
        ]
        costs = [
            {"partner_id": [2, "Beta renamed"], "cost": 30},
            {"partner_id": [3, "Gamma"], "cost": 7},
            {"partner_id": False, "cost": 1},
        ]
        self.assertEqual(
            merge_rows(self._merge("inner"), sales, costs),
            [
                {"partner_id": [2, "Beta"], "revenue": 50, "cost": 30},
                {"partner_id": False, "revenue": 5, "cost": 1},
            ],
        )
        self.assertEqual(
            merge_rows(self._merge("left"), sales, costs),
            [
                {"partner_id": [1, "Acme"], "revenue": 100, "cost": None},
                {"partner_id": [2, "Beta"], "revenue": 50, "cost": 30},
                {"partner_id": False, "revenue": 5, "cost": 1},
            ],
        )
        self.assertEqual(
            merge_rows(self._merge("full"), sales, costs),
            [
                {"partner_id": [1, "Acme"], "revenue": 100, "cost": None},
                {"partner_id": [2, "Beta"], "revenue": 50, "cost": 30},
                {"partner_id": False, "revenue": 5, "cost": 1},
                {"partner_id": [3, "Gamma"], "revenue": None, "cost": 7},
            ],
        )

    def test_merge_key_takes_a_many2one_by_its_id(self):
        self.assertEqual(merge_key({"a": [4, "Four"], "b": "March 2026"}, ["a", "b"]), (4, "March 2026"))
        self.assertEqual([_key_part(value) for value in ([7, "x"], [], False, "x")], [7, [], False, "x"])


COLUMNS_BY_DATASET = {"by_month": ["month", "revenue", "orders"], "top": ["partner_id", "revenue"]}
ELEMENT_IDS = {"total", "months", "chart", "march"}


def bindings(values=(), tables=(), charts=()):
    return {"values": list(values), "tables": list(tables), "charts": list(charts)}


def value_binding(**values):
    return {
        "id": "total",
        "dataset": "by_month",
        "column": "revenue",
        "pick": "sum",
        "match_column": "",
        "match_value": "",
        "format": "number",
        "decimals": 0,
        **values,
    }


def table_binding(**values):
    return {
        "id": "months",
        "dataset": "by_month",
        "columns": [{"column": "month", "label": "Month", "format": "text", "decimals": 0}],
        "limit": 0,
        "total_label": "Total",
        **values,
    }


def chart_binding(**values):
    return {
        "id": "chart",
        "type": "bar",
        "stacked": False,
        "dataset": "by_month",
        "label_column": "month",
        "series": [{"column": "revenue", "label": "Revenue"}],
        **values,
    }


@tagged("post_install", "-at_install")
class TestReportBindings(BaseCase):
    def test_validate_bindings_accepts_values_tables_and_charts(self):
        validate_bindings(
            bindings(
                [value_binding(), value_binding(id="march", pick="first", match_column="month", match_value="March")],
                [table_binding()],
                [chart_binding(type="horizontalBar", stacked=True)],
            ),
            COLUMNS_BY_DATASET,
            ELEMENT_IDS,
        )
        validate_bindings(bindings(), COLUMNS_BY_DATASET, set())

    def test_validate_bindings_refuses_what_the_renderer_cannot_fill(self):
        cases = {
            "not an object": [],
            "an extra kind": {**bindings(), "scripts": []},
            "values not a list": {**bindings(), "values": {}},
            "too many charts": bindings(charts=[chart_binding(id=f"c{index}") for index in range(11)]),
            "an extra key": bindings([{**value_binding(), "onclick": "x()"}]),
            "a bad id": bindings([value_binding(id="1st")]),
            "an id the page lacks": bindings([value_binding(id="nowhere")]),
            "one element twice": bindings([value_binding()], charts=[chart_binding(id="total")]),
            "an unknown dataset": bindings([value_binding(dataset="sales")]),
            "an unknown column": bindings([value_binding(column="margin")]),
            "an unknown pick": bindings([value_binding(pick="median")]),
            "an unknown match column": bindings([value_binding(match_column="country")]),
            "a match value not text": bindings([value_binding(match_value=3)]),
            "an unknown format": bindings([value_binding(format="currency")]),
            "too many decimals": bindings([value_binding(decimals=7)]),
            "boolean decimals": bindings([value_binding(decimals=True)]),
            "a table without columns": bindings(tables=[table_binding(columns=[])]),
            "a table column of another dataset": bindings(
                tables=[
                    table_binding(columns=[{"column": "partner_id", "label": "P", "format": "text", "decimals": 0}])
                ]
            ),
            "a long label": bindings(
                tables=[
                    table_binding(columns=[{"column": "month", "label": "x" * 81, "format": "text", "decimals": 0}])
                ]
            ),
            "a negative limit": bindings(tables=[table_binding(limit=-1)]),
            "a total label not text": bindings(tables=[table_binding(total_label=None)]),
            "an unknown chart": bindings(charts=[chart_binding(type="radar")]),
            "stacked not a boolean": bindings(charts=[chart_binding(stacked="yes")]),
            "an unknown label column": bindings(charts=[chart_binding(label_column="day")]),
            "no series": bindings(charts=[chart_binding(series=[])]),
            "a series of an unknown column": bindings(
                charts=[chart_binding(series=[{"column": "cost", "label": "C"}])]
            ),
        }
        for case, value in cases.items():
            with self.subTest(case), self.assertRaises(ReportSpecError):
                validate_bindings(value, COLUMNS_BY_DATASET, ELEMENT_IDS)

    def test_binding_checks_one_by_one(self):
        binding = value_binding()
        self.assertEqual(_binding_columns(binding, COLUMNS_BY_DATASET), COLUMNS_BY_DATASET["by_month"])
        _check_binding_column(binding, "revenue", COLUMNS_BY_DATASET["by_month"])
        _check_format(binding, "percent", 6)
        _check_label(binding, "x" * 80)
        _check_value_binding(binding, COLUMNS_BY_DATASET)
        _check_table_binding(table_binding(), COLUMNS_BY_DATASET)
        _check_chart_binding(chart_binding(type="doughnut"), COLUMNS_BY_DATASET)
        refused = (
            (_check_format, (binding, "percent", -1)),
            (_check_label, (binding, "x" * 81)),
            (_check_binding_column, (binding, "cost", ["revenue"])),
            (_binding_columns, (value_binding(dataset="sales"), COLUMNS_BY_DATASET)),
        )
        for check, arguments in refused:
            with self.subTest(check.__name__), self.assertRaises(ReportSpecError):
                check(*arguments)

    def test_spec_columns_names_every_dataset_and_merge(self):
        sales = dataset(formulas=[{"name": "double", "expression": "revenue * 2"}])
        costs = dataset("costs", measures=["cost:sum(amount_untaxed)"])
        self.assertEqual(
            spec_columns(spec(sales, costs, merges=[merge()])),
            {
                "sales": ["partner_id", "revenue", "double"],
                "costs": ["partner_id", "cost"],
                "joined": ["partner_id", "revenue", "double", "cost"],
            },
        )
