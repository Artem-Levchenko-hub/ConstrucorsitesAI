"""Controller filesystem for legacy adapter doubles; production guards still execute."""

import pytest

from yleum_orchestrator.services.cell_state import CellStateStore
from yleum_orchestrator.services.machine_adapter import MachineAdapter


@pytest.fixture(autouse=True)
def legacy_machine_controller(tmp_path, monkeypatch):
    original = MachineAdapter.__init__

    def initialize(self, manager, settings):
        if not hasattr(manager, "state_store"):
            manager.state_store = CellStateStore(tmp_path / "controller-state")
            manager.state_store.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        original(self, manager, settings)

    monkeypatch.setattr(MachineAdapter, "__init__", initialize)
