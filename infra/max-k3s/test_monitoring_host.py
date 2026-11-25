import importlib.util
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "monitoring_host", Path(__file__).with_name("monitoring-host.py")
)
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)


class MonitoringHostTest(unittest.TestCase):
    def test_selects_the_preferred_default_interface(self):
        routes = [
            {"dev": "backup", "metric": 200},
            {"dev": 'invalid"device', "metric": 0},
            {"dev": "enp1s0", "metric": 100},
        ]
        with patch.object(monitor, "command", return_value=json.dumps(routes)):
            self.assertEqual(monitor.external_device(), "enp1s0")

    def test_backup_age_and_counts_use_terminal_receipts(self):
        now = datetime(2026, 11, 25, 3, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(monitor.backup_samples(root, now), [])
            for i, days, success in ((0, 3, True), (1, 1, False)):
                run_id = f"{i:032x}"
                start = (now - timedelta(days=days)).replace(hour=0, minute=15)
                end = start + timedelta(minutes=1)
                folder = root / start.date().isoformat() / run_id
                folder.mkdir(parents=True)
                (folder / "HOST-SNAPSHOT.json").write_text(json.dumps({
                    "run_id": run_id, "scope": "host-artifact-snapshot",
                    "started_at_utc": start.isoformat(), "checked_at_utc": end.isoformat(),
                    "status": "complete" if success else "failed",
                    "exit_code": 0 if success else 1,
                }), encoding="utf-8")
            rows = monitor.backup_samples(root, now)
        success_at = datetime(2026, 11, 22, 0, 16, tzinfo=UTC).timestamp()
        self.assertIn(f"yleum_backup_last_verified_timestamp_seconds {success_at:.3f}", rows)
        self.assertIn('yleum_backup_window_results{result="failed",window="168h"} 1', rows)
        self.assertIn('yleum_backup_window_results{result="missed",window="168h"} 2', rows)
        self.assertIn('yleum_backup_window_results{result="missed",window="24h"} 1', rows)

    def test_only_complete_snapshot_replaces_existing_file(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "snapshot.prom"
            monitor.publish(output, b"metric 1\n")
            monitor.publish(output, b"metric 2\n")
            self.assertEqual(output.read_bytes(), b"metric 2\n")
            self.assertEqual(list(output.parent.iterdir()), [output])


if __name__ == "__main__":
    unittest.main()
