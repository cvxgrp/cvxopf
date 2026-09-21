"""Forbidden dependency gate for the production streaming boundary."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from importlib.util import resolve_name
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent
PRODUCTION_STREAMING_MODULES = (
    ROOT / "audit.py",
    ROOT / "p0_fixture.py",
    ROOT / "streaming_schema.py",
    ROOT / "streaming_runner.py",
    ROOT / "streaming_archive.py",
    ROOT / "streaming_driver.py",
)
FORBIDDEN_MODULE_PREFIXES = (
    "cvxopf._hierarchical_solver",
    "experiments.hierarchical_battery_resilience",
)
PRODUCTION_PACKAGE = "experiments.case118_annual_hierarchy"


@dataclass(frozen=True, order=True)
class ImportViolation:
    """One forbidden dependency found in a production source file."""

    path: str
    line: int
    imported_module: str
    import_form: str


@dataclass(frozen=True)
class ImportGateReport:
    """Complete result of the frozen production-module scan."""

    scanned_modules: tuple[str, ...]
    violations: tuple[ImportViolation, ...]

    @property
    def passed(self) -> bool:
        return not self.violations


def _is_forbidden(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in FORBIDDEN_MODULE_PREFIXES
    )


def _imported_names(node: ast.Import | ast.ImportFrom) -> Iterable[str]:
    if isinstance(node, ast.Import):
        yield from (alias.name for alias in node.names)
        return
    base = node.module or ""
    if node.level:
        base = resolve_name(f"{'.' * node.level}{base}", PRODUCTION_PACKAGE)
    for alias in node.names:
        yield f"{base}.{alias.name}" if base else alias.name


def _literal_dynamic_import(
    node: ast.Call,
    *,
    importlib_aliases: set[str],
    import_module_aliases: set[str],
) -> str | None:
    """Return a literal dynamic-import target, including common aliases."""
    if not node.args or not isinstance(node.args[0], ast.Constant):
        return None
    target = node.args[0].value
    if not isinstance(target, str):
        return None
    function = node.func
    if isinstance(function, ast.Name) and (
        function.id == "__import__" or function.id in import_module_aliases
    ):
        return target
    if (
        isinstance(function, ast.Attribute)
        and function.attr == "import_module"
        and isinstance(function.value, ast.Name)
        and function.value.id in importlib_aliases
    ):
        return target
    return None


def scan_source(source: str, *, path: str = "<source>") -> tuple[ImportViolation, ...]:
    """Scan one Python source string for forbidden dependency edges."""
    tree = ast.parse(source, filename=path)
    violations: list[ImportViolation] = []
    importlib_aliases = {"importlib"}
    import_module_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    importlib_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
            for alias in node.names:
                if alias.name == "import_module":
                    import_module_aliases.add(alias.asname or alias.name)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for module in _imported_names(node):
                if _is_forbidden(module):
                    violations.append(
                        ImportViolation(path, node.lineno, module, "static")
                    )
        elif isinstance(node, ast.Call):
            dynamic_module = _literal_dynamic_import(
                node,
                importlib_aliases=importlib_aliases,
                import_module_aliases=import_module_aliases,
            )
            if dynamic_module is not None and _is_forbidden(dynamic_module):
                violations.append(
                    ImportViolation(
                        path, node.lineno, dynamic_module, "dynamic_literal"
                    )
                )
    return tuple(sorted(violations))


def run_import_gate(
    paths: tuple[Path, ...] = PRODUCTION_STREAMING_MODULES,
) -> ImportGateReport:
    """Scan exactly the frozen production streaming module registry."""
    violations: list[ImportViolation] = []
    scanned: list[str] = []
    for path in paths:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(ROOT)
        except ValueError as exc:
            raise ValueError(f"Production module lies outside gate root: {path}") from exc
        if not resolved.is_file():
            raise ValueError(f"Production module does not exist: {path}")
        display = relative.as_posix()
        scanned.append(display)
        violations.extend(scan_source(resolved.read_text(), path=display))
    return ImportGateReport(tuple(scanned), tuple(sorted(violations)))


__all__ = [
    "FORBIDDEN_MODULE_PREFIXES",
    "PRODUCTION_STREAMING_MODULES",
    "ImportGateReport",
    "ImportViolation",
    "run_import_gate",
    "scan_source",
]
