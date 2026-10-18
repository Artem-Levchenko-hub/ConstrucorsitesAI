"""The privileged consumers must recognize exactly what live readers advertise."""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PRODUCER = ROOT / "apps/api/src/yleum_api/services/generation/recovery_readers.py"
CONSUMERS = (
    ROOT / "infra/release/generation-reader-inventory.py",
    ROOT
    / "apps/orchestrator/src/yleum_orchestrator/services/generation_reader_inventory.py",
)


def capability_tuple(path: Path, name: str) -> tuple[str, ...]:
    definitions = [
        node.value
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
    ]
    if len(definitions) != 1:
        raise ValueError(f"Expected one {name} definition in {path.name}")
    value = ast.literal_eval(definitions[0])
    if not isinstance(value, tuple) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Expected a literal capability tuple in {path.name}")
    return value


class ReaderCapabilityParityTests(unittest.TestCase):
    def test_live_capabilities_match_both_privileged_consumers(self) -> None:
        for name in ("API_CAPABILITIES", "CONTROLLER_CAPABILITIES"):
            advertised = capability_tuple(PRODUCER, name)
            self.assertEqual(advertised, tuple(sorted(set(advertised))))
            for consumer in CONSUMERS:
                with self.subTest(capabilities=name, consumer=consumer.relative_to(ROOT)):
                    self.assertEqual(capability_tuple(consumer, name), advertised)


if __name__ == "__main__":
    unittest.main()
