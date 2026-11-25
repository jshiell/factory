import ast
from pathlib import Path

from factory import failure_codes

SRC = Path(failure_codes.__file__).parent
STAGE_MODULES = (
    "step", "agent_call", "readonly_stage", "classify",
    "gate", "verify_task", "worktree_clean",
    "plan_task", "review_plan", "audit_task", "audit_eval", "audit_gaps",
    "approve_plan", "abandon_task", "recover_task",
)  # fmt: skip
NOT_CODES = {"FIX_PREFIX", "FIX_SENTENCE_PREFIX"}
CODES = {
    value
    for name, value in vars(failure_codes).items()
    if name.isupper() and name not in NOT_CODES and isinstance(value, str)
}


def _literals(module: str) -> list[tuple[int, str]]:
    tree = ast.parse((SRC / f"{module}.py").read_text())
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.ClassDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
    }
    dict_keys = {id(key) for node in ast.walk(tree) if isinstance(node, ast.Dict) for key in node.keys}
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and node.value in CODES and id(node) not in docstrings | dict_keys
    ]


def test_stage_modules_use_the_constants_not_literals():
    found = {module: _literals(module) for module in STAGE_MODULES}

    assert {module: hits for module, hits in found.items() if hits} == {}
