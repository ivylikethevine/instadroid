"""The rule that every local variable is annotated where it's first bound, which no linter
enforces (ruff's ANN rules stop at signatures, basedpyright only reports a local whose type it
can't infer). `local-annotations` (this module's console script, in scripts/check.sh's python
group) and tests/test_typing_policy.py run it over the whole tree; given files, it checks those:

    local-annotations                                          # every Python file in the repository
    local-annotations app/instadroid/scrape.py tests/test_db.py

The rule, per function body (nested functions have their own scope; module and class bodies are
left to basedpyright's inference):

- the first binding of a name by `=`, by tuple unpacking, by `:=` or by `with ... as` must be an
  annotated assignment (`name: T = ...`), or come after a bare declaration (`name: T`) in the same
  function;
- a later rebinding needs nothing more;
- `for` targets, comprehension variables, `except ... as`, imports, `def`/`class` names, parameters
  and `global`/`nonlocal` names are exempt: Python has no annotated form for them, or they're
  typed elsewhere."""

import ast
import sys
from collections.abc import Iterator
from pathlib import Path


def _names(target: ast.expr) -> Iterator[str]:
    """Every plain name an assignment target binds (`a`, `a, b`, `[a, *rest]`); attribute and
    subscript targets bind nothing new."""
    match target:
        case ast.Name(id=name):
            yield name
        case ast.Tuple(elts=elts) | ast.List(elts=elts):
            for e in elts:
                yield from _names(e)
        case ast.Starred(value=value):
            yield from _names(value)
        case _:
            return


class _Scope:
    def __init__(self, path: Path, declared: set[str]) -> None:
        self.path = path
        self.declared = declared
        self.offences: list[str] = []

    def bind(self, name: str, node: ast.AST, text: str) -> None:
        if name not in self.declared:
            self.declared.add(name)
            self.offences.append(f"{self.path}:{getattr(node, 'lineno', '?')}: {text}")


def _walk_expr(scope: _Scope, node: ast.AST, line: str) -> None:
    """Walrus targets anywhere inside an expression, outermost first; comprehensions are their own
    scope and are skipped."""
    for child in ast.walk(node):
        if isinstance(child, ast.NamedExpr):
            scope.bind(child.target.id, child, line)


def _check_body(scope: _Scope, body: list[ast.stmt], lines: list[str]) -> list[str]:
    """Walk statements in order, binding names as Python would, and return the offences."""
    found: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, set[str]]] = []
    for stmt in body:
        _visit(scope, stmt, lines, found)
    offences: list[str] = list(scope.offences)
    for func, _ in found:
        offences += _check_function(scope.path, func, lines)
    return offences


def _visit(
    scope: _Scope,
    stmt: ast.stmt,
    lines: list[str],
    nested: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, set[str]]],
) -> None:
    text: str = lines[stmt.lineno - 1].strip() if stmt.lineno <= len(lines) else ""
    match stmt:
        case ast.FunctionDef() | ast.AsyncFunctionDef():
            scope.declared.add(stmt.name)
            nested.append((stmt, set()))
            return  # its body is its own scope
        case ast.ClassDef():
            scope.declared.add(stmt.name)
            return
        case ast.Import(names=names) | ast.ImportFrom(names=names):
            for alias in names:
                scope.declared.add((alias.asname or alias.name).split(".")[0])
            return
        case ast.Global(names=names) | ast.Nonlocal(names=names):
            scope.declared.update(names)
            return
        case ast.AnnAssign(target=ast.Name(id=name), value=value):
            if value is not None:
                _walk_expr(scope, value, text)
            scope.declared.add(name)
            return
        case ast.Assign(targets=targets, value=value):
            _walk_expr(scope, value, text)
            for t in targets:
                for name in _names(t):
                    scope.bind(name, stmt, text)
            return
        case ast.AugAssign(value=value):
            _walk_expr(scope, value, text)
            return
        case ast.For(target=target, iter=iter_) | ast.AsyncFor(target=target, iter=iter_):
            _walk_expr(scope, iter_, text)
            scope.declared.update(_names(target))  # no annotated form: exempt
        case ast.With(items=items) | ast.AsyncWith(items=items):
            for item in items:
                _walk_expr(scope, item.context_expr, text)
                if item.optional_vars is not None:
                    for name in _names(item.optional_vars):
                        scope.bind(name, stmt, text)
        case ast.Try(handlers=handlers) | ast.TryStar(handlers=handlers):
            for h in handlers:
                if h.name:
                    scope.declared.add(h.name)
        case ast.Match(subject=subject, cases=cases):
            _walk_expr(scope, subject, text)
            for case in cases:
                for node in ast.walk(case.pattern):
                    if isinstance(node, ast.MatchAs | ast.MatchStar) and node.name:
                        scope.declared.add(node.name)
                    elif isinstance(node, ast.MatchMapping) and node.rest:
                        scope.declared.add(node.rest)
        case ast.If(test=test) | ast.While(test=test):
            _walk_expr(scope, test, text)
        case ast.Expr(value=value) | ast.Return(value=value) if value is not None:
            _walk_expr(scope, value, text)
        case ast.Assert(test=test):
            _walk_expr(scope, test, text)
        case _:
            pass
    children: list[ast.stmt] = []
    if isinstance(
        stmt, ast.If | ast.For | ast.AsyncFor | ast.While | ast.With | ast.AsyncWith | ast.Try | ast.TryStar
    ):
        children += stmt.body
    if isinstance(stmt, ast.If | ast.For | ast.AsyncFor | ast.While | ast.Try | ast.TryStar):
        children += stmt.orelse
    if isinstance(stmt, ast.Try | ast.TryStar):
        children += stmt.finalbody
        for h in stmt.handlers:
            children += h.body
    if isinstance(stmt, ast.Match):
        for case in stmt.cases:
            children += case.body
    for child in children:
        _visit(scope, child, lines, nested)


def _check_function(path: Path, func: ast.FunctionDef | ast.AsyncFunctionDef, lines: list[str]) -> list[str]:
    args: ast.arguments = func.args
    params: set[str] = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    for extra in (args.vararg, args.kwarg):
        if extra is not None:
            params.add(extra.arg)
    return _check_body(_Scope(path, params), func.body, lines)


def _functions(body: list[ast.stmt]) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Top-level functions and methods (one level of class nesting, as the code base has)."""
    for stmt in body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            yield stmt
        elif isinstance(stmt, ast.ClassDef):
            yield from _functions(stmt.body)


def unannotated_locals(path: Path) -> list[str]:
    """`file:line: source` for every local bound without an annotation in `path`."""
    source: str = path.read_text()
    lines: list[str] = source.splitlines()
    return [o for func in _functions(ast.parse(source).body) for o in _check_function(path, func, lines)]


def repository_sources() -> list[Path]:
    """Every Python file the rule covers: app/, tests/ and the CI helpers in .github/scripts."""
    root: Path = Path(__file__).resolve().parents[2]
    return sorted(
        p
        for top in ("app", "tests", ".github/scripts")
        for p in (root / top).rglob("*.py")
        if "__pycache__" not in p.parts
    )


def main(argv: list[str] | None = None) -> int:
    args: list[str] = sys.argv[1:] if argv is None else argv
    given: list[Path] = [Path(a) for a in args]
    paths: list[Path] = (
        [f for p in given for f in (sorted(p.rglob("*.py")) if p.is_dir() else [p])]
        if given
        else repository_sources()
    )
    offences: list[str] = [o for p in paths for o in unannotated_locals(p)]
    for o in offences:
        print(o)
    print(f"total {len(offences)} in {len(paths)} file(s)")
    return 1 if offences else 0


if __name__ == "__main__":
    sys.exit(main())
