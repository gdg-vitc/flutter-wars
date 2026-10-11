"""Keep feature modules behind their shared gateway contracts."""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"


def _owner(name: str) -> str | None:
    parts = name.split(".")
    if parts[:2] == ["app", "modules"] and len(parts) > 2:
        return None if parts[2] == "__init__" else parts[2]
    if len(parts) > 1 and parts[0] == "app" and parts[1] in ("trading", "auction"):
        return parts[1]
    return None


def test_modules_import_only_their_own_implementation():
    violations = []
    for path in APP.rglob("*.py"):
        source = ".".join(path.relative_to(APP.parent).with_suffix("").parts)
        owner = _owner(source)
        if owner is None:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            imports = []
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imports = [node.module or ""]
                imports += [f"{node.module}.{alias.name}" for alias in node.names]
            for target in imports:
                other = _owner(target)
                if other is not None and other != owner:
                    violations.append(f"{path.relative_to(APP)}:{node.lineno}: {target}")
    assert not violations, "Cross-module implementation imports:\n" + "\n".join(violations)


def test_contracts_and_core_do_not_import_feature_implementations():
    violations = []
    for base in (APP / "contracts", APP / "core"):
        for path in base.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                targets = []
                if isinstance(node, ast.Import):
                    targets = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    targets = [node.module or ""]
                for target in targets:
                    if _owner(target) is not None:
                        violations.append(f"{path.relative_to(APP)}:{node.lineno}: {target}")
    assert not violations, "\n".join(violations)
