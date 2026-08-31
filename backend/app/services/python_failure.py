"""Deterministic diagnostics for model-generated Python failures."""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_TRACEBACK_LOCATION = re.compile(r'^\s*File "([^"]+)", line (\d+)(?:, in .*)?$')
_EXCEPTION_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)):\s*(.*)$")
_UNSTABLE_VALUE = re.compile(r"\b(?:0x[0-9a-fA-F]+|\d{4,}|exec_[A-Za-z0-9]+)\b")


@dataclass(frozen=True)
class PythonFailure:
    source: str
    exception_type: str
    message: str
    normalized_message: str
    script: str
    line: int | None
    failing_line: str | None
    code_excerpt: str
    fingerprint: str
    semantic_fingerprint: str

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "exception_type": self.exception_type,
            "message": self.message,
            "normalized_message": self.normalized_message,
            "script": self.script,
            "line": self.line,
            "failing_line": self.failing_line,
            "code_excerpt": self.code_excerpt,
            "fingerprint": self.fingerprint,
            "semantic_fingerprint": self.semantic_fingerprint,
        }


@dataclass(frozen=True)
class DataFrameDependencyIssue:
    dataframe: str
    producer_line: int
    projected_columns: tuple[str, ...]
    missing_columns: tuple[str, ...]
    downstream_references: tuple[tuple[str, int], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "dataframe": self.dataframe,
            "producer_line": self.producer_line,
            "projected_columns": list(self.projected_columns),
            "missing_columns": list(self.missing_columns),
            "downstream_references": [
                {"column": column, "line": line} for column, line in self.downstream_references
            ],
        }


@dataclass(frozen=True)
class ReadOnlyWorkspaceWriteIssue:
    path: str
    line: int

    def as_dict(self) -> dict[str, object]:
        return {"path": self.path, "line": self.line}


@dataclass(frozen=True)
class DataFrameTruthinessIssue:
    dataframe: str
    line: int

    def as_dict(self) -> dict[str, object]:
        return {"dataframe": self.dataframe, "line": self.line}


_READ_ONLY_WORKSPACE_DIRECTORIES = frozenset(
    {"input", "context", "plans", "scripts", "analysis"}
)
_PATH_WRITE_METHODS = frozenset(
    {"write_text", "write_bytes", "mkdir", "touch", "unlink", "rmdir", "rename", "replace"}
)
_SERIALIZER_METHODS = frozenset({"to_csv", "to_json", "to_excel", "to_pickle", "to_parquet"})
_PANDAS_DATAFRAME_METHODS = frozenset({
    "assign", "copy", "drop", "drop_duplicates", "dropna", "merge", "pivot",
    "pivot_table", "rename", "reset_index", "sort_values", "sort_index",
})
_PANDAS_CONSTRUCTORS = frozenset({"DataFrame", "read_csv", "read_excel", "read_json", "read_parquet"})


@dataclass
class _ProjectionState:
    dataframe: str
    producer_line: int
    projected_columns: set[str]
    derived_columns: set[str]
    missing_references: dict[str, int]


def script_fingerprint(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def parse_python_failure(
    stderr: str,
    script_path: str,
    code: str,
    *,
    source: str = "docker",
    exception_type: str | None = None,
    message: str | None = None,
    line: int | None = None,
) -> PythonFailure:
    lines = stderr.splitlines()
    locations: list[tuple[str, int]] = []
    for value in lines:
        match = _TRACEBACK_LOCATION.match(value)
        if match:
            locations.append((match.group(1), int(match.group(2))))
    if line is None and locations:
        matching = [item for item in locations if Path(item[0]).name == Path(script_path).name]
        line = (matching or locations)[-1][1]

    if exception_type is None:
        for value in reversed(lines):
            match = _EXCEPTION_LINE.match(value.strip())
            if match:
                exception_type, parsed_message = match.groups()
                message = message if message is not None else parsed_message
                break
    exception_type = exception_type or "PythonExecutionError"
    if message is None:
        message = stderr.strip().splitlines()[-1] if stderr.strip() else "unknown error"
    normalized = normalize_failure_message(message)
    source_lines = code.splitlines()
    failing_line = source_lines[line - 1] if line and line <= len(source_lines) else None
    excerpt = _code_excerpt(source_lines, line)
    location = f"{Path(script_path).name}:{line or 'unknown'}"
    semantic = f"{exception_type}|{normalized}"
    return PythonFailure(
        source=source,
        exception_type=exception_type,
        message=message,
        normalized_message=normalized,
        script=script_path,
        line=line,
        failing_line=failing_line,
        code_excerpt=excerpt,
        fingerprint=f"{semantic}|{location}",
        semantic_fingerprint=semantic,
    )


def normalize_failure_message(message: str) -> str:
    normalized = " ".join(message.strip().split())
    normalized = normalized.replace("/workspace/", "")
    return _UNSTABLE_VALUE.sub("<value>", normalized)[:500]


def preflight_failure(
    code: str,
    script_path: str,
    *,
    check_dataframe_dependencies: bool = False,
) -> PythonFailure | None:
    try:
        tree = ast.parse(code, filename=script_path)
    except SyntaxError as exc:
        stderr = f"SyntaxError: {exc.msg}"
        return parse_python_failure(
            stderr,
            script_path,
            code,
            source="preflight",
            exception_type="SyntaxError",
            message=exc.msg,
            line=exc.lineno,
        )

    invalid_literals = sorted(
        {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id in {"false", "true", "null"}
        }
    )
    if invalid_literals:
        invalid = invalid_literals[0]
        node = next(
            item
            for item in ast.walk(tree)
            if isinstance(item, ast.Name) and item.id == invalid and isinstance(item.ctx, ast.Load)
        )
        message = (
            f"JSON literal '{invalid}' is not valid in Python; use "
            + {"false": "False", "true": "True", "null": "None"}[invalid]
        )
        return parse_python_failure(
            f"PythonPreflightError: {message}",
            script_path,
            code,
            source="preflight",
            exception_type="PythonPreflightError",
            message=message,
            line=node.lineno,
        )

    truthiness_issues = dataframe_truthiness_issues(code, tree=tree)
    if truthiness_issues:
        issue = truthiness_issues[0]
        message = (
            f"Pandas object '{issue.dataframe}' cannot be used in a boolean condition. "
            "Use is not None for presence, .empty for DataFrame/Series emptiness, "
            ".any() for any-match, or .all() for all-match."
        )
        return parse_python_failure(
            f"DataFrameTruthinessError: {message}",
            script_path,
            code,
            source="preflight",
            exception_type="DataFrameTruthinessError",
            message=message,
            line=issue.line,
        )

    readonly_issues = readonly_workspace_write_issues(code, tree=tree)
    if readonly_issues:
        issue = readonly_issues[0]
        message = (
            f"Generated Python attempted to write protected workspace directory "
            f"'{issue.path}/'. Write new outputs only under data/, charts/, or generated/; "
            "the application owns input/, context/, plans/, scripts/, and analysis/."
        )
        return parse_python_failure(
            f"ReadOnlyWorkspaceWriteError: {message}",
            script_path,
            code,
            source="preflight",
            exception_type="ReadOnlyWorkspaceWriteError",
            message=message,
            line=issue.line,
        )

    if not check_dataframe_dependencies:
        return None
    issues = dataframe_dependency_issues(code, tree=tree)
    if not issues:
        return None
    issue = issues[0]
    references = ", ".join(
        f"{column} (line {line})" for column, line in issue.downstream_references
    )
    message = (
        f"DataFrame '{issue.dataframe}' projects columns at line {issue.producer_line} "
        f"but later references unavailable columns: {references}. Repair the producing "
        "projection or explicitly derive the columns before use."
    )
    line = min(line for _, line in issue.downstream_references)
    return parse_python_failure(
        f"PythonSchemaDependencyError: {message}",
        script_path,
        code,
        source="preflight",
        exception_type="PythonSchemaDependencyError",
        message=message,
        line=line,
    )


def dataframe_truthiness_issues(
    code: str, *, tree: ast.Module | None = None
) -> list[DataFrameTruthinessIssue]:
    """Find direct boolean conditions on values proven or strongly inferred as pandas objects."""

    if tree is None:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []
    visitor = _DataFrameTruthinessVisitor()
    visitor.collect(tree)
    return visitor.issues


class _DataFrameTruthinessVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.dataframe_names: set[str] = set()
        self.function_parameters: dict[str, list[str]] = {}
        self.parameter_dataframes: set[tuple[str, str]] = set()
        self.issues: list[DataFrameTruthinessIssue] = []
        self._seen: set[tuple[str, int]] = set()

    def collect(self, tree: ast.Module) -> None:
        self._collect_dataframe_assignments(tree)
        self._collect_function_parameters(tree)
        self._collect_dataframe_call_bindings(tree)
        self.visit(tree)

    def _collect_dataframe_assignments(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                if not _looks_like_dataframe(value, self.dataframe_names):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        self.dataframe_names.add(target.id)

    def _collect_function_parameters(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.function_parameters[node.name] = [arg.arg for arg in node.args.args]

    def _collect_dataframe_call_bindings(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            parameters = self.function_parameters.get(node.func.id)
            if not parameters:
                continue
            for index, argument in enumerate(node.args):
                if index < len(parameters) and _looks_like_dataframe(argument, self.dataframe_names):
                    self.parameter_dataframes.add((node.func.id, parameters[index]))
            for keyword in node.keywords:
                if keyword.arg in parameters and _looks_like_dataframe(
                    keyword.value, self.dataframe_names
                ):
                    self.parameter_dataframes.add((node.func.id, keyword.arg))

    def visit_If(self, node: ast.If) -> None:
        self._check(node.test, node.lineno)
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self._check(node.test, node.lineno)
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        self._check(node.test, node.lineno)
        self.generic_visit(node)

    def _check(self, test: ast.expr, line: int) -> None:
        for name in _bare_boolean_names(test):
            if name in self.dataframe_names:
                self._add(name, line)
        dataframe_parameters = {parameter for _, parameter in self.parameter_dataframes}
        for name in _bare_boolean_names(test):
            if name in dataframe_parameters:
                self._add(name, line)


    def _add(self, name: str, line: int) -> None:
        key = (name, line)
        if key not in self._seen:
            self._seen.add(key)
            self.issues.append(DataFrameTruthinessIssue(name, line))


def _bare_boolean_names(node: ast.expr) -> set[str]:
    """Return names used directly as truthy operands, not names inside comparisons/calls."""

    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.BoolOp):
        names: set[str] = set()
        for value in node.values:
            names.update(_bare_boolean_names(value))
        return names
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _bare_boolean_names(node.operand)
    return set()


def _looks_like_dataframe(node: ast.expr | None, dataframe_names: set[str]) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return node.id in dataframe_names
    if isinstance(node, ast.Call):
        name = _qualified_name(node.func)
        if name and name.split(".")[-1] in _PANDAS_CONSTRUCTORS:
            return True
        return isinstance(node.func, ast.Attribute) and (
            isinstance(node.func.value, ast.Name) and node.func.value.id in dataframe_names
            or _looks_like_dataframe(node.func.value, dataframe_names)
        )
    if isinstance(node, ast.Attribute):
        return _looks_like_dataframe(node.value, dataframe_names)
    if isinstance(node, ast.Subscript):
        return isinstance(node.value, ast.Name) and node.value.id in dataframe_names
    return False


def readonly_workspace_write_issues(
    code: str, *, tree: ast.Module | None = None
) -> list[ReadOnlyWorkspaceWriteIssue]:
    """Find statically provable writes into Docker-mounted read-only directories."""

    if tree is None:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []
    visitor = _ReadOnlyWorkspaceWriteVisitor()
    visitor.visit(tree)
    return visitor.issues


class _ReadOnlyWorkspaceWriteVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.path_bindings: dict[str, str] = {}
        self.issues: list[ReadOnlyWorkspaceWriteIssue] = []
        self._seen: set[tuple[str, int]] = set()

    def visit_Assign(self, node: ast.Assign) -> None:
        value = _workspace_path(node.value, self.path_bindings)
        if value is not None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.path_bindings[target.id] = value
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        value = _workspace_path(node.value, self.path_bindings)
        if value is not None and isinstance(node.target, ast.Name):
            self.path_bindings[node.target.id] = value
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        function_name = _qualified_name(node.func)
        if function_name in {"tempfile.mkstemp", "tempfile.NamedTemporaryFile"}:
            directory = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "dir"),
                None,
            )
            self._check(directory, node)
        elif function_name in {"open", "io.open"} and node.args:
            mode = "r"
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                mode = str(node.args[1].value)
            for keyword in node.keywords:
                if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                    mode = str(keyword.value.value)
            if any(flag in mode for flag in ("w", "a", "x", "+")):
                self._check(node.args[0], node)
        elif function_name in {"os.replace", "os.rename", "shutil.move"} and len(node.args) >= 2:
            self._check(node.args[1], node)
        elif isinstance(node.func, ast.Attribute):
            if node.func.attr in _PATH_WRITE_METHODS:
                self._check(node.func.value, node)
            elif node.func.attr in _SERIALIZER_METHODS and node.args:
                self._check(node.args[0], node)
        self.generic_visit(node)

    def _check(self, node: ast.expr | None, owner: ast.AST) -> None:
        path = _workspace_path(node, self.path_bindings)
        top_level = _read_only_workspace_directory(path)
        if top_level is None:
            return
        key = (top_level, owner.lineno)
        if key in self._seen:
            return
        self._seen.add(key)
        self.issues.append(ReadOnlyWorkspaceWriteIssue(top_level, owner.lineno))


def _workspace_path(node: ast.expr | None, bindings: dict[str, str]) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.replace("\\", "/").strip("/")
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _workspace_path(node.left, bindings)
        right = _workspace_path(node.right, bindings)
        if left is None or right is None:
            return None
        return f"{left.rstrip('/')}/{right.lstrip('/')}"
    if isinstance(node, ast.Call):
        name = _qualified_name(node.func)
        if name in {"Path", "pathlib.Path"} and node.args:
            return _workspace_path(node.args[0], bindings)
        if name == "str" and node.args:
            return _workspace_path(node.args[0], bindings)
    return None


def _qualified_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _read_only_workspace_directory(path: str | None) -> str | None:
    if not path:
        return None
    normalized = path.replace("\\", "/").lstrip("/")
    if normalized.startswith("workspace/"):
        normalized = normalized[len("workspace/") :]
    top_level = normalized.split("/", 1)[0]
    return top_level if top_level in _READ_ONLY_WORKSPACE_DIRECTORIES else None


def dataframe_dependency_issues(
    code: str, *, tree: ast.Module | None = None
) -> list[DataFrameDependencyIssue]:
    """Find provable missing columns after a literal top-level DataFrame projection.

    This intentionally covers only common, deterministic pandas patterns. Dynamic schemas,
    aliases, branches, and function-local dataflow are left to runtime rather than guessed.
    """

    if tree is None:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []

    list_bindings: dict[str, tuple[str, ...]] = {}
    states: dict[str, _ProjectionState] = {}
    completed: list[_ProjectionState] = []

    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue

        for name, state in list(states.items()):
            for column, line in _dataframe_references(statement, name, list_bindings):
                if column not in state.projected_columns | state.derived_columns:
                    state.missing_references.setdefault(column, line)

        for target, columns in _derived_column_assignments(statement, list_bindings):
            if target in states:
                states[target].derived_columns.update(columns)

        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            value = statement.value
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                literal_columns = _literal_columns(value, list_bindings)
                if literal_columns is not None:
                    list_bindings[target.id] = literal_columns
                elif target.id in list_bindings:
                    del list_bindings[target.id]

                projected = _projection_columns(value, list_bindings)
                if target.id in states:
                    completed.append(states.pop(target.id))
                if projected is not None:
                    states[target.id] = _ProjectionState(
                        dataframe=target.id,
                        producer_line=statement.lineno,
                        projected_columns=set(projected),
                        derived_columns=set(),
                        missing_references={},
                    )

    completed.extend(states.values())
    issues: list[DataFrameDependencyIssue] = []
    for state in completed:
        if not state.missing_references:
            continue
        references = tuple(
            sorted(state.missing_references.items(), key=lambda item: (item[1], item[0]))
        )
        issues.append(
            DataFrameDependencyIssue(
                dataframe=state.dataframe,
                producer_line=state.producer_line,
                projected_columns=tuple(sorted(state.projected_columns)),
                missing_columns=tuple(sorted(state.missing_references)),
                downstream_references=references,
            )
        )
    return sorted(issues, key=lambda item: (item.producer_line, item.dataframe))


def _projection_columns(
    value: ast.expr | None, list_bindings: dict[str, tuple[str, ...]]
) -> tuple[str, ...] | None:
    current = value
    while current is not None:
        if isinstance(current, ast.Call):
            current = current.func
            continue
        if isinstance(current, ast.Attribute):
            current = current.value
            continue
        if isinstance(current, ast.Subscript):
            columns = _literal_columns(current.slice, list_bindings)
            if columns is not None:
                return columns
            current = current.value
            continue
        break
    return None


def _derived_column_assignments(
    statement: ast.stmt, list_bindings: dict[str, tuple[str, ...]]
) -> list[tuple[str, tuple[str, ...]]]:
    assignments: list[tuple[str, tuple[str, ...]]] = []
    targets: list[ast.expr] = []
    if isinstance(statement, ast.Assign):
        targets = statement.targets
    elif isinstance(statement, (ast.AnnAssign, ast.AugAssign)):
        targets = [statement.target]
    for target in targets:
        if not isinstance(target, ast.Subscript) or not isinstance(target.value, ast.Name):
            continue
        columns = _literal_columns(target.slice, list_bindings)
        if columns is not None:
            assignments.append((target.value.id, columns))
    return assignments


def _dataframe_references(
    statement: ast.stmt,
    dataframe: str,
    list_bindings: dict[str, tuple[str, ...]],
) -> list[tuple[str, int]]:
    visitor = _DataFrameReferenceVisitor(dataframe, list_bindings)
    visitor.visit(statement)
    return visitor.references


class _DataFrameReferenceVisitor(ast.NodeVisitor):
    _SCHEMA_CHANGING_METHODS = {"assign", "join", "merge", "pivot", "pivot_table", "rename"}
    _COLUMN_METHOD_ARGS = {
        "drop_duplicates": ("subset",),
        "dropna": ("subset",),
        "get": (),
        "groupby": ("by",),
        "merge": ("on", "left_on"),
        "pivot": ("index", "columns", "values"),
        "pivot_table": ("index", "columns", "values"),
        "set_index": ("keys",),
        "sort_values": ("by",),
    }

    def __init__(self, dataframe: str, list_bindings: dict[str, tuple[str, ...]]) -> None:
        self.dataframe = dataframe
        self.list_bindings = list_bindings
        self.references: list[tuple[str, int]] = []

    def visit_Subscript(self, node: ast.Subscript) -> Any:
        if (
            isinstance(node.ctx, ast.Load)
            and isinstance(node.value, ast.Name)
            and node.value.id == self.dataframe
        ):
            self._add(_literal_columns(node.slice, self.list_bindings), node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> Any:
        if isinstance(node.func, ast.Attribute) and _root_name(node.func.value) == self.dataframe:
            method = node.func.attr
            schema_changed_upstream = bool(
                _method_chain(node.func.value) & self._SCHEMA_CHANGING_METHODS
            )
            if schema_changed_upstream:
                self.generic_visit(node)
                return
            if method in self._COLUMN_METHOD_ARGS:
                if node.args:
                    self._add(_literal_columns(node.args[0], self.list_bindings), node.lineno)
                for keyword in node.keywords:
                    if keyword.arg in self._COLUMN_METHOD_ARGS[method]:
                        self._add(_literal_columns(keyword.value, self.list_bindings), node.lineno)
            elif method in {"agg", "aggregate"}:
                for keyword in node.keywords:
                    value = keyword.value
                    if isinstance(value, (ast.Tuple, ast.List)) and value.elts:
                        self._add(_literal_columns(value.elts[0], self.list_bindings), node.lineno)
                if node.args and isinstance(node.args[0], ast.Dict):
                    self._add(_literal_columns(node.args[0], self.list_bindings), node.lineno)
        self.generic_visit(node)

    def _add(self, columns: tuple[str, ...] | None, line: int) -> None:
        for column in columns or ():
            self.references.append((column, line))


def _literal_columns(
    node: ast.expr | None, list_bindings: dict[str, tuple[str, ...]]
) -> tuple[str, ...] | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (node.value,)
    if isinstance(node, ast.Name):
        return list_bindings.get(node.id)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values: list[str] = []
        for item in node.elts:
            columns = _literal_columns(item, list_bindings)
            if columns is None:
                return None
            values.extend(columns)
        return tuple(values)
    if isinstance(node, ast.Dict):
        values: list[str] = []
        for key in node.keys:
            columns = _literal_columns(key, list_bindings)
            if columns is None:
                return None
            values.extend(columns)
        return tuple(values)
    return None


def _root_name(node: ast.AST) -> str | None:
    current = node
    while True:
        if isinstance(current, ast.Name):
            return current.id
        if isinstance(current, ast.Attribute):
            current = current.value
            continue
        if isinstance(current, ast.Call):
            current = current.func
            continue
        if isinstance(current, ast.Subscript):
            current = current.value
            continue
        return None


def _method_chain(node: ast.AST) -> set[str]:
    methods: set[str] = set()
    current = node
    while True:
        if isinstance(current, ast.Call):
            current = current.func
            continue
        if isinstance(current, ast.Attribute):
            methods.add(current.attr)
            current = current.value
            continue
        if isinstance(current, ast.Subscript):
            current = current.value
            continue
        return methods


def _code_excerpt(lines: list[str], line: int | None, radius: int = 3) -> str:
    if not lines:
        return ""
    if line is None:
        start, end = 0, min(len(lines), 12)
    else:
        start, end = max(0, line - radius - 1), min(len(lines), line + radius)
    return "\n".join(f"{index + 1}: {lines[index]}" for index in range(start, end))
