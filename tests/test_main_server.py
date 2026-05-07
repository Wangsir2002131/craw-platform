"""Tests for main_server background thread wiring."""

from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from platform import main_server


class FakeLoopWorker:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    def beat(self, *args, **kwargs) -> None:
        return None

    def clear(self) -> None:
        return None

    def find_stale_consumers(self, *args, **kwargs):
        return []

    def run(self, *, stop_event: threading.Event | None = None, **_kwargs) -> int:
        while stop_event is None or not stop_event.is_set():
            stop_event.wait(0.02)
        return 0


class FakeDispatcher:
    def dispatch_once(self, limit: int = 100) -> int:
        return 0


class FakeConsumerManager:
    def __init__(self) -> None:
        self.configured = None
        self.started = False
        self.shutdown_called = False

    def configure(self, *, enabled: bool, default_consumer_count: int | None = None) -> None:
        self.configured = (enabled, default_consumer_count)

    def start_defaults(self) -> None:
        self.started = True

    def shutdown(self, *args, **kwargs) -> None:
        self.shutdown_called = True


class TestMainServer(unittest.TestCase):
    def test_start_background_threads_includes_result_listener(self):
        fake_manager = FakeConsumerManager()
        args = SimpleNamespace(
            managed_consumers=False,
            default_consumers_per_model=1,
            api_only=False,
            heartbeat_interval=10,
            health_check_interval=30,
            stale_after=60,
            interval=5,
            limit=100,
        )

        with (
            patch.object(main_server, "MasterHeartbeat", FakeLoopWorker),
            patch.object(main_server, "HealthChecker", FakeLoopWorker),
            patch.object(main_server, "ResultListener", FakeLoopWorker),
            patch.object(main_server, "_build_dispatcher", return_value=FakeDispatcher()),
            patch.object(main_server, "get_consumer_manager", return_value=fake_manager),
        ):
            stop_event, threads = main_server._start_background_threads(args)
            try:
                thread_names = {thread.name for thread in threads}
                self.assertIn("result-listener", thread_names)
                self.assertIn("dispatcher-loop", thread_names)
                self.assertEqual((False, 1), fake_manager.configured)
                self.assertTrue(fake_manager.started)
            finally:
                stop_event.set()
                for thread in threads:
                    thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
