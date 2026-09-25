"""Generation services must remain usable without importing the HTTP router."""

import ast
from pathlib import Path

SOURCE = Path(__file__).parents[1] / "src" / "yleum_api"


def test_messages_router_does_not_own_generation_execution() -> None:
    tree = ast.parse((SOURCE / "routers" / "messages.py").read_text(encoding="utf-8"))
    definitions = {
        node.name for node in tree.body if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
    }
    assert not definitions.intersection(
        {"_process_prompt", "_run_tracked_prompt", "_run_text_turn"}
    )


def test_generation_services_do_not_import_http_router() -> None:
    package = SOURCE / "services" / "generation"
    assert package.is_dir()
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module != "yleum_api.routers.messages", path
                if node.module == "yleum_api.routers":
                    assert "messages" not in {alias.name for alias in node.names}, path
