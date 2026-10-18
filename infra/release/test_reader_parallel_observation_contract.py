"""Two host observations overlap; partial results never authorize readiness."""
from __future__ import annotations

import importlib.util
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch


class ParallelObservationTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).with_name("generation-reader-inventory.py")
        spec = importlib.util.spec_from_file_location("parallel_observer_contract", source)
        self.observer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.observer)
        self.config = {"host": "core", "release_sha": "a" * 40,
                       "api_image": "sha256:" + "b" * 64, "ssh_user": "root"}
        self.registrations = {"fixed": "registration fixture"}
        self.redis = Mock(
            get=Mock(return_value="11111111-1111-4111-8111-111111111111"),
            info=Mock(return_value={"run_id": "c" * 40}),
        )
        self.snapshot = {"state": "ready"}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in (
            ("redis_connection", Mock(return_value=self.redis)),
            ("registrations_now", Mock(return_value=self.registrations)),
            ("build_snapshot", Mock(return_value=self.snapshot)),
            ("distribute", Mock()),
            ("deny_snapshot", Mock()),
        ):
            self.stack.enter_context(patch.object(self.observer, name, value))
        self.clock = self.stack.enter_context(
            patch.object(self.observer.time, "time_ns", side_effect=[1_000_000_000])
        )

    def collectors(self, local, remote):
        self.stack.enter_context(patch.object(self.observer, "collect_host", local))
        self.stack.enter_context(patch.object(self.observer, "remote", remote))

    def test_both_full_collections_overlap_before_build_or_publish(self):
        barrier = threading.Barrier(2, timeout=2)

        def local(**kwargs):
            self.clock.assert_called_once()
            self.assertIs(kwargs["registrations"], self.registrations)
            self.assertEqual(kwargs["host"], "core")
            self.observer.build_snapshot.assert_not_called()
            barrier.wait()
            return [{"host": "core"}]

        def remote(action, payload, user):
            self.clock.assert_called_once()
            self.assertEqual((action, user), ("collect", "root"))
            self.assertIs(payload["registrations"], self.registrations)
            self.observer.build_snapshot.assert_not_called()
            barrier.wait()
            return {"observed": [{"host": "commerce"}]}

        self.collectors(local, remote)
        self.assertIs(self.observer.scan(self.config), self.snapshot)
        self.observer.build_snapshot.assert_called_once()
        args = self.observer.build_snapshot.call_args.kwargs
        self.assertEqual(args["observations"], [{"host": "core"}, {"host": "commerce"}])
        self.assertEqual(args["observed_at_ms"], 1000)
        self.assertIs(args["registrations"], self.registrations)
        self.clock.assert_called_once()
        self.observer.distribute.assert_called_once()
        self.observer.deny_snapshot.assert_not_called()

    def test_either_failure_revokes_before_waiting_for_other_host(self):
        for failed in ["core", "commerce"]:
            with self.subTest(failed=failed):
                waiting = threading.Event()
                released = threading.Event()
                finished = threading.Event()

                def observe(host):
                    if host == failed:
                        self.assertTrue(waiting.wait(2))
                        raise ValueError("host observation failed")
                    waiting.set()
                    try:
                        self.assertTrue(released.wait(2), "denial waited for the slow host")
                    finally:
                        finished.set()
                    return [{"host": host}]

                def deny(config, redis):
                    self.assertFalse(finished.is_set(), "denial must precede executor cleanup")
                    self.assertIs(redis, self.redis)
                    released.set()

                self.observer.deny_snapshot.side_effect = deny
                def local(**kwargs):
                    return observe("core")

                def remote(*args):
                    return {"observed": observe("commerce")}
                # The previous subtest's patches are harmlessly shadowed.
                self.collectors(local, remote)
                self.clock.side_effect = [1_000_000_000]
                with self.assertRaisesRegex(ValueError, "host observation failed"):
                    self.observer.scan(self.config)
                self.assertTrue(finished.is_set(), "read-only workers were not joined")
                self.observer.build_snapshot.assert_not_called()
                self.observer.distribute.assert_not_called()

    def test_invalid_remote_shape_cannot_publish(self):
        self.collectors(lambda **kwargs: [], lambda *args: {"observed": [], "extra": True})
        with self.assertRaises(ValueError):
            self.observer.scan(self.config)
        self.observer.deny_snapshot.assert_called_once_with(self.config, self.redis)
        self.observer.build_snapshot.assert_not_called()
        self.observer.distribute.assert_not_called()

    def test_final_validation_failure_still_denies(self):
        self.collectors(lambda **kwargs: [], lambda *args: {"observed": []})
        self.observer.build_snapshot.side_effect = ValueError("expired or incompatible")
        with self.assertRaisesRegex(ValueError, "expired or incompatible"):
            self.observer.scan(self.config)
        self.observer.deny_snapshot.assert_called_once_with(self.config, self.redis)
        self.observer.distribute.assert_not_called()

    def test_distribution_failure_still_denies(self):
        self.collectors(lambda **kwargs: [], lambda *args: {"observed": []})
        self.observer.distribute.side_effect = ValueError("remote mirror not confirmed")
        with self.assertRaisesRegex(ValueError, "remote mirror not confirmed"):
            self.observer.scan(self.config)
        self.observer.deny_snapshot.assert_called_once_with(self.config, self.redis)


if __name__ == "__main__":
    unittest.main()

