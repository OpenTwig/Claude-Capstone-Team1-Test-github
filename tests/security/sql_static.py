"""Static check for SQL built from untrusted text (task 5.1; N2.1).

Every `.execute(...)` / `.executemany(...)` call in `app/` must pass SQL whose text is fixed when the
code is written. User values go in the separate parameters, never into the SQL string. Rather than
banning f-strings outright (the app uses them for fixed column lists and `%s` placeholder lists),
the check traces where each piece of the SQL comes from, and accepts only:

- string literals;
- f-strings and `+` concatenations whose pieces are themselves safe;
- `", ".join(["%s"] * n)`-style placeholder lists (a literal joined over a list of literals);
- module-level constants assigned from safe values;
- local variables whose every assignment (including `+=` and tuple unpacking) is safe;
- calls to functions in the same module whose every `return` value is safe (the first element, if
  the function returns a tuple);
- a function's parameter, when every call to that function in the same module passes safe SQL.

Anything else fails: `%` formatting, `.format()`, subscripts, attributes, and calls to other code.
A line that truly can't use parameters (e.g. an identifier or keyword) can be exempted with a
`# sql-safe: <reason>` comment on the call; the reason is required, so each exemption is reviewed.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

EXEMPT_MARK = "# sql-safe:"
SQL_METHODS = {"execute", "executemany"}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    code: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: SQL built from text that isn't fixed: {self.code.strip()}"


class _Module:
    def __init__(self, source: str):
        self.source = source
        self.lines = source.splitlines()
        self.tree = ast.parse(source)
        self.functions: dict[str, ast.FunctionDef] = {}
        self.constants: dict[str, list[ast.expr]] = {}
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions[node.name] = node
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.constants.setdefault(target.id, []).append(node.value)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                self.constants.setdefault(node.target.id, []).append(node.value)
        self.parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(self.tree):
            for child in ast.iter_child_nodes(parent):
                self.parents[child] = parent

    def enclosing_function(self, node: ast.AST) -> ast.FunctionDef | None:
        while node in self.parents:
            node = self.parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node
        return None

    # --- the tracing -----------------------------------------------------------------------------------

    def safe(self, expr: ast.expr, scope: ast.FunctionDef | None, seen: frozenset = frozenset()) -> bool:
        if isinstance(expr, ast.Constant):
            return isinstance(expr.value, str)
        if isinstance(expr, ast.JoinedStr):
            return all(
                self.safe(part.value, scope, seen) and part.format_spec is None
                for part in expr.values if isinstance(part, ast.FormattedValue)
            )
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
            return self.safe(expr.left, scope, seen) and self.safe(expr.right, scope, seen)
        if isinstance(expr, ast.IfExp):
            return self.safe(expr.body, scope, seen) and self.safe(expr.orelse, scope, seen)
        if isinstance(expr, ast.Call):
            return self._safe_call(expr, scope, seen)
        if isinstance(expr, ast.Name):
            return self._safe_name(expr.id, scope, seen)
        return False  # %-formatting, .format(), subscripts, attributes, ...

    def _safe_list(self, expr: ast.expr, scope, seen) -> bool:
        """A list of literals, or such a list repeated (["%s"] * n)."""
        if isinstance(expr, (ast.List, ast.Tuple)):
            return all(self.safe(e, scope, seen) for e in expr.elts)
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Mult):
            return self._safe_list(expr.left, scope, seen) or self._safe_list(expr.right, scope, seen)
        return False

    def _safe_call(self, call: ast.Call, scope, seen) -> bool:
        func = call.func
        if (isinstance(func, ast.Attribute) and func.attr == "join" and isinstance(func.value, ast.Constant)
                and len(call.args) == 1 and self._safe_list(call.args[0], scope, seen)):
            return True
        if isinstance(func, ast.Name) and func.id in self.functions:
            return self._safe_returns(self.functions[func.id], 0, seen, whole=True)
        return False

    def _safe_returns(self, function: ast.FunctionDef, index: int, seen, whole: bool = False) -> bool:
        key = ("returns", function.name, index, whole)
        if key in seen:
            return True  # already being checked further up: judged there
        seen = seen | {key}
        returns = [n for n in ast.walk(function) if isinstance(n, ast.Return) and self.enclosing_function(n) is function]
        if not returns:
            return False
        for ret in returns:
            value = ret.value
            if value is None:
                return False
            if isinstance(value, ast.Tuple):
                value = value.elts[index] if index < len(value.elts) else None
                if value is None:
                    return False
            elif not whole and index != 0:
                return False
            if not self.safe(value, function, seen):
                return False
        return True

    def _safe_name(self, name: str, scope, seen) -> bool:
        if scope is not None:
            assignments = self._assignments(scope, name)
            if assignments is not None:
                key = ("local", scope.name, name)
                if key in seen:
                    return True
                seen = seen | {key}
                return all(check(seen) for check in assignments)
            if name in {a.arg for a in scope.args.args + scope.args.kwonlyargs + scope.args.posonlyargs}:
                return self._safe_parameter(scope, name, seen)
        if name in self.constants:
            return all(self.safe(value, None, seen) for value in self.constants[name])
        return False

    def _assignments(self, scope: ast.FunctionDef, name: str):
        """Checks for every assignment to `name` in this function, or None if it isn't assigned there."""
        checks = []
        for node in ast.walk(scope):
            if self.enclosing_function(node) is not scope:
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id == name and node.value is not None:
                        checks.append(lambda seen, v=node.value: self.safe(v, scope, seen))
                    elif isinstance(target, ast.Tuple):
                        for index, element in enumerate(target.elts):
                            if isinstance(element, ast.Name) and element.id == name:
                                checks.append(lambda seen, v=node.value, i=index: self._safe_element(v, i, scope, seen))
            elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
                checks.append(lambda seen, v=node.value, op=node.op: isinstance(op, ast.Add) and self.safe(v, scope, seen))
            elif isinstance(node, (ast.For, ast.With, ast.NamedExpr)) and name in {
                    n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}:
                checks.append(lambda seen: False)  # loop variables, `as` targets, walrus: not traced
        return checks or None

    def _safe_element(self, value: ast.expr, index: int, scope, seen) -> bool:
        if isinstance(value, ast.Tuple):
            return index < len(value.elts) and self.safe(value.elts[index], scope, seen)
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id in self.functions:
            return self._safe_returns(self.functions[value.func.id], index, seen)
        return False

    def _safe_parameter(self, function: ast.FunctionDef, name: str, seen) -> bool:
        key = ("param", function.name, name)
        if key in seen:
            return True
        seen = seen | {key}
        params = [a.arg for a in function.args.posonlyargs + function.args.args]
        position = params.index(name) if name in params else None
        calls = [n for n in ast.walk(self.tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == function.name]
        if not calls:
            return False
        for call in calls:
            passed = next((k.value for k in call.keywords if k.arg == name), None)
            if passed is None and position is not None and position < len(call.args):
                passed = call.args[position]
            if passed is None:
                default = self._default(function, name)
                if default is None or not self.safe(default, None, seen):
                    return False
                continue
            if not self.safe(passed, self.enclosing_function(call), seen):
                return False
        return True

    @staticmethod
    def _default(function: ast.FunctionDef, name: str):
        positional = function.args.posonlyargs + function.args.args
        defaults = dict(zip([a.arg for a in positional][-len(function.args.defaults):], function.args.defaults)) \
            if function.args.defaults else {}
        defaults.update({a.arg: d for a, d in zip(function.args.kwonlyargs, function.args.kw_defaults) if d is not None})
        return defaults.get(name)

    # --- the scan ----------------------------------------------------------------------------------------

    def exempt(self, call: ast.Call) -> bool:
        """A `# sql-safe: <reason>` comment on the call, or on a comment line directly above it."""
        numbers = list(range(call.lineno, (call.end_lineno or call.lineno) + 1))
        if call.lineno > 1 and self.lines[call.lineno - 2].strip().startswith("#"):
            numbers.append(call.lineno - 1)
        for number in numbers:
            line = self.lines[number - 1]
            if EXEMPT_MARK in line and line.split(EXEMPT_MARK, 1)[1].strip():
                return True
        return False

    def findings(self, path: str) -> list[Finding]:
        found = []
        for node in ast.walk(self.tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in SQL_METHODS and node.args):
                if self.exempt(node):
                    continue
                if not self.safe(node.args[0], self.enclosing_function(node)):
                    found.append(Finding(path, node.lineno, self.lines[node.lineno - 1]))
        return found


def check_source(source: str, path: str = "<string>") -> list[Finding]:
    return _Module(source).findings(path)


def check_tree(root: Path) -> list[Finding]:
    found = []
    for path in sorted(root.rglob("*.py")):
        found += check_source(path.read_text(encoding="utf-8"), str(path.relative_to(root.parent)))
    return found
