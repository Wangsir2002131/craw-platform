"""Tests for in-process consumer manager scaling."""

from __future__ import annotations

import threading
import time
import unittest

from platform.consumers.manager import ConsumerManager


class FakeManagedConsumer:
    def __init__(self, *_args, **_kwargs) -> None:
        self.consumer_id = f"fake-{id(self)}"
        self.queue_name = "queue:fake"

    def run(self, *, stop_event: threading.Event | None = None, **_kwargs) -> int:
        while stop_event is None or not stop_event.is_set():
            time.sleep(0.02)
        return 0


class TestConsumerManager(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = ConsumerManager(default_consumer_count=0)
        self.manager._CONSUMER_CLASSES = {"fake": FakeManagedConsumer}
        self.manager._workers = {"fake": []}
        self.manager._desired_counts = {"fake": 0}
        self.manager.configure(enabled=True, default_consumer_count=0)

    def tearDown(self) -> None:
        self.manager.shutdown(join_timeout=1.0)

    def test_increment_starts_worker(self):
        status = self.manager.increment("fake")

        self.assertEqual(1, status["desiredConsumers"])
        self.assertEqual(1, status["activeConsumers"])
        self.assertEqual(0, status["drainingConsumers"])

    def test_decrement_drains_worker(self):
        self.manager.scale_to("fake", 2)
        drained = self.manager.decrement("fake")

        self.assertEqual(1, drained["desiredConsumers"])
        self.assertEqual(1, drained["activeConsumers"])
        self.assertEqual(1, drained["drainingConsumers"])

        deadline = time.time() + 1.0
        while time.time() < deadline:
            status = self.manager.status("fake")
            if status["drainingConsumers"] == 0:
                break
            time.sleep(0.05)

        final_status = self.manager.status("fake")
        self.assertEqual(1, final_status["desiredConsumers"])
        self.assertEqual(1, final_status["activeConsumers"])
        self.assertEqual(0, final_status["drainingConsumers"])


if __name__ == "__main__":
    unittest.main()