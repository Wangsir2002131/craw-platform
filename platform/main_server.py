#!/usr/bin/env python
"""Integrated master service entry point."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from platform.account.real_account_sync import sync_real_accounts  # noqa: E402
from platform.alerts import (  # noqa: E402
    AccountMonitor,
    QueueMonitor,
    SystemMonitor,
    TaskMonitor,
    get_alert_manager,
)
from platform.alerts.alert_manager import register_monitor  # noqa: E402
from platform.alerts.monitors.base import BaseMonitor  # noqa: E402
from platform.api.routes.account import dashboard_router as dashboard_account_router  # noqa: E402
from platform.api.routes.account import router as account_router  # noqa: E402
from platform.api.routes.alert import router as alert_router  # noqa: E402
from platform.api.routes.control import get_control_state  # noqa: E402
from platform.api.routes.control import router as control_router  # noqa: E402
from platform.api.routes.health_status import router as health_status_router  # noqa: E402
from platform.api.routes.log import router as log_router  # noqa: E402
from platform.api.routes.queue import dashboard_router as dashboard_queue_router  # noqa: E402
from platform.api.routes.queue import router as queue_router  # noqa: E402
from platform.api.routes.stats import router as stats_router  # noqa: E402
from platform.api.routes.task import dashboard_router as dashboard_task_router  # noqa: E402
from platform.api.routes.task import router as task_router  # noqa: E402
from platform.config import (  # noqa: E402
    BATCH_SIZE,
    CRAWLER_MODULES,
    DB_CONFIG,
    DISPATCH_INTERVAL,
    EXECUTE_CRAWLERS,
    REDIS_URL,
)
from platform.consumers.manager import get_consumer_manager  # noqa: E402
from platform.dispatcher.master_dispatcher import MasterDispatcher  # noqa: E402
from platform.heartbeat.health_checker import HealthChecker  # noqa: E402
from platform.heartbeat.master_heartbeat import MasterHeartbeat  # noqa: E402
from platform.logging_config import configure_file_logging, route_uvicorn_logs_to_root  # noqa: E402
from platform.tasks.result_listener import ResultListener  # noqa: E402


logger = logging.getLogger(__name__)


def configure_logging() -> None:
    configure_file_logging("master_server.log", level=logging.INFO, force=True)
    route_uvicorn_logs_to_root()


def build_api_app() -> FastAPI:
    app = FastAPI(
        title="Crawler Platform Service",
        description="Integrated API and dispatcher service for the crawler platform.",
        version="0.1.0",
    )
    app.include_router(task_router)
    app.include_router(queue_router)
    app.include_router(account_router)
    app.include_router(alert_router)
    app.include_router(control_router)
    app.include_router(health_status_router)
    app.include_router(log_router)
    app.include_router(stats_router)
    app.include_router(dashboard_account_router)
    app.include_router(dashboard_queue_router)
    app.include_router(dashboard_task_router)
    app.mount("/pages", StaticFiles(directory=str(Path(__file__).resolve().parent.parent / "pages")), name="pages")

    @app.get("/")
    async def root() -> FileResponse:
        return FileResponse(Path(__file__).resolve().parent.parent / "dashboard.html")

    @app.get("/dashboard.html")
    async def dashboard() -> FileResponse:
        return FileResponse(Path(__file__).resolve().parent.parent / "dashboard.html")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        control_state = get_control_state().snapshot()
        return {
            "status": "healthy",
            "dispatcher_paused": control_state["paused"],
            "restart_requested": control_state["restart_requested"],
        }

    return app


app = build_api_app()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Crawler platform integrated master service")
    parser.add_argument("--once", action="store_true", help="run one dispatch cycle")
    parser.add_argument("--forever", action="store_true", help="run dispatcher loop and API server")
    parser.add_argument("--api-only", action="store_true", help="run API server only")
    parser.add_argument("--dispatcher-only", action="store_true", help="run dispatcher only")
    parser.add_argument("--limit", type=int, default=BATCH_SIZE, help="max rows per cycle")
    parser.add_argument("--interval", type=int, default=DISPATCH_INTERVAL, help="dispatcher loop interval seconds")
    parser.add_argument("--host", default="127.0.0.1", help="API bind host")
    parser.add_argument("--port", type=int, default=8000, help="API bind port")
    parser.add_argument("--heartbeat-interval", type=int, default=10, help="master heartbeat interval seconds")
    parser.add_argument("--health-check-interval", type=int, default=30, help="health checker interval seconds")
    parser.add_argument("--stale-after", type=int, default=60, help="consumer stale threshold seconds")
    parser.add_argument(
        "--managed-consumers",
        action="store_true",
        help="let main_server manage in-process consumers and enable dashboard +/- scaling",
    )
    parser.add_argument(
        "--default-consumers-per-model",
        type=int,
        default=1,
        help="default managed consumer count per model when --managed-consumers is enabled",
    )
    return parser


def run_dispatch_loop(
    dispatcher: MasterDispatcher,
    *,
    interval: int,
    limit: int,
    stop_event: threading.Event,
) -> None:
    control_state = get_control_state()
    pause_logged = False
    while not stop_event.is_set():
        if control_state.paused:
            if not pause_logged:
                logger.info("dispatcher paused by control API")
                pause_logged = True
        else:
            pause_logged = False
            try:
                dispatched = dispatcher.dispatch_once(limit=limit)
                if dispatched > 0:
                    logger.info("dispatch cycle completed: %s units", dispatched)
            except RuntimeError as exc:
                logger.warning("dispatch skipped: %s", exc)
            except Exception:
                logger.exception("dispatch failed")
        stop_event.wait(interval)


def run_master_heartbeat(
    heartbeat: MasterHeartbeat,
    *,
    interval: int,
    stop_event: threading.Event,
) -> None:
    control_state = get_control_state()
    while not stop_event.is_set():
        status = "paused" if control_state.paused else "running"
        heartbeat.beat(status=status, extra={"restart_requested": control_state.restart_requested})
        stop_event.wait(interval)
    heartbeat.clear()


def run_health_checker(
    checker: HealthChecker,
    *,
    interval: int,
    stale_after: int,
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        try:
            stale_consumers = checker.find_stale_consumers(stale_after_seconds=stale_after)
            if stale_consumers:
                logger.warning("detected stale consumers: %s", stale_consumers)
        except Exception:
            logger.exception("health check failed")
        stop_event.wait(interval)


def run_result_listener(
    listener: ResultListener,
    *,
    timeout: int,
    idle_sleep: float,
    stop_event: threading.Event,
) -> None:
    try:
        listener.run(
            timeout=timeout,
            idle_sleep=idle_sleep,
            stop_event=stop_event,
        )
    except Exception:
        logger.exception("result listener failed")


def run_api_server(host: str, port: int) -> None:
    uvicorn.run(app, host=host, port=port, log_level="info", log_config=None, access_log=True)


def _build_dispatcher() -> MasterDispatcher:
    return MasterDispatcher(
        DB_CONFIG,
        crawler_modules=CRAWLER_MODULES,
        execute_crawlers=EXECUTE_CRAWLERS,
    )


def _start_alert_monitors() -> list[BaseMonitor]:
    """Initialize and start all alert monitors."""
    alert_manager = get_alert_manager()

    monitors: list[BaseMonitor] = [
        QueueMonitor(alert_manager=alert_manager, interval=30),
        TaskMonitor(alert_manager=alert_manager, interval=60),
        AccountMonitor(alert_manager=alert_manager, interval=60),
        SystemMonitor(alert_manager=alert_manager, interval=60),
    ]
    for monitor in monitors:
        register_monitor(monitor)
        monitor.start()
    logger.info("alert monitors started: %s", [m.monitor_name for m in monitors])
    return monitors


def _stop_alert_monitors(monitors: list[BaseMonitor]) -> None:
    for monitor in monitors:
        try:
            monitor.stop(timeout=2.0)
        except Exception:
            logger.exception("failed to stop monitor %s", monitor.monitor_name)


def _start_background_threads(
    args: argparse.Namespace,
) -> tuple[threading.Event, list[threading.Thread], list[BaseMonitor]]:
    stop_event = threading.Event()
    heartbeat = MasterHeartbeat(redis_url=REDIS_URL)
    checker = HealthChecker(redis_url=REDIS_URL)
    dispatcher = _build_dispatcher()
    result_listener = ResultListener(DB_CONFIG)
    consumer_manager = get_consumer_manager()
    consumer_manager.configure(
        enabled=args.managed_consumers,
        default_consumer_count=args.default_consumers_per_model,
    )
    consumer_manager.start_defaults()
    monitors = _start_alert_monitors()

    threads = [
        threading.Thread(
            target=run_master_heartbeat,
            kwargs={"heartbeat": heartbeat, "interval": args.heartbeat_interval, "stop_event": stop_event},
            daemon=True,
            name="master-heartbeat",
        ),
        threading.Thread(
            target=run_health_checker,
            kwargs={
                "checker": checker,
                "interval": args.health_check_interval,
                "stale_after": args.stale_after,
                "stop_event": stop_event,
            },
            daemon=True,
            name="health-checker",
        ),
        threading.Thread(
            target=run_result_listener,
            kwargs={
                "listener": result_listener,
                "timeout": 5,
                "idle_sleep": 1.0,
                "stop_event": stop_event,
            },
            daemon=True,
            name="result-listener",
        ),
    ]

    if not args.api_only:
        threads.append(
            threading.Thread(
                target=run_dispatch_loop,
                kwargs={
                    "dispatcher": dispatcher,
                    "interval": args.interval,
                    "limit": args.limit,
                    "stop_event": stop_event,
                },
                daemon=True,
                name="dispatcher-loop",
            )
        )

    for thread in threads:
        thread.start()

    return stop_event, threads, monitors


def sync_accounts_on_startup() -> None:
    count = sync_real_accounts(DB_CONFIG)
    logger.info("real account sync completed: %s accounts", count)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)

    if args.api_only and args.dispatcher_only:
        logger.error("--api-only and --dispatcher-only cannot be used together")
        return 2

    try:
        sync_accounts_on_startup()
    except Exception:
        logger.exception("real account sync failed on startup")
        return 1

    if args.api_only:
        logger.info("starting API server only on %s:%s", args.host, args.port)
        monitors = _start_alert_monitors()
        try:
            run_api_server(args.host, args.port)
        finally:
            _stop_alert_monitors(monitors)
        return 0

    if not args.forever and not args.dispatcher_only:
        dispatcher = _build_dispatcher()
        try:
            logger.info("running one dispatch cycle")
            dispatched = dispatcher.dispatch_once(limit=args.limit)
            logger.info("dispatch cycle completed: %s units", dispatched)
        except RuntimeError as exc:
            logger.warning("dispatch skipped: %s", exc)
        except Exception:
            logger.exception("dispatch failed")
            return 1
        return 0

    if args.dispatcher_only and not args.forever:
        args.forever = True

    stop_event, threads, monitors = _start_background_threads(args)
    try:
        if args.dispatcher_only:
            logger.info("starting dispatcher service only")
            while True:
                time.sleep(1)
        else:
            logger.info("starting integrated API and dispatcher service on %s:%s", args.host, args.port)
            run_api_server(args.host, args.port)
    except KeyboardInterrupt:
        logger.info("service interrupted, shutting down")
    finally:
        stop_event.set()
        for thread in threads:
            thread.join(timeout=2)
        _stop_alert_monitors(monitors)
        get_consumer_manager().shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
