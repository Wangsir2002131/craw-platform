#!/usr/bin/env python
"""Run the DeepSeek Redis consumer directly."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from platform.config import DB_CONFIG  # noqa: E402
from platform.consumers.deepseek_consumer import DeepseekConsumer  # noqa: E402
from platform.consumers.supervisor import ConsumerSupervisor  # noqa: E402
from platform.queue.protocol import get_queue_name  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run DeepSeek Redis consumer.")
    parser.add_argument("--once", action="store_true", help="Only consume at most one message, then exit.")
    parser.add_argument("--timeout", type=int, default=5, help="Redis BRPOP timeout seconds.")
    parser.add_argument("--idle-sleep", type=float, default=1.0, help="Sleep seconds between empty polls.")
    parser.add_argument("--log-level", default="INFO", help="Logging level, e.g. DEBUG/INFO/WARNING.")
    return parser


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def main() -> int:
    args = build_parser().parse_args()
    configure_logging(args.log_level)

    logging.getLogger(__name__).info(
        "starting deepseek consumer runner: queue=%s priority_queue=%s timeout=%s idle_sleep=%s",
        get_queue_name("deepseek"),
        f"{get_queue_name('deepseek')}:priority",
        args.timeout,
        args.idle_sleep,
    )
    if args.once:
        consumer = DeepseekConsumer(DB_CONFIG)
        processed = consumer.run(
            once=True,
            timeout=args.timeout,
            idle_sleep=args.idle_sleep,
        )
        print(f"deepseek consumer stopped, processed={processed}")
        return 0
    supervisor = ConsumerSupervisor(
        model_key="deepseek",
        consumer_factory=lambda: DeepseekConsumer(DB_CONFIG),
        timeout=args.timeout,
        idle_sleep=args.idle_sleep,
    )
    return supervisor.run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
