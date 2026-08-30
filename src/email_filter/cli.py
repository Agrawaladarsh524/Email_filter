"""Command line entrypoints: the standalone poll loop and the API server."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from types import FrameType

from .config import get_settings

log = logging.getLogger(__name__)

_stop = False


def _handle_signal(signum: int, _frame: FrameType | None) -> None:
    global _stop
    log.info("signal %s received; finishing current cycle", signum)
    _stop = True


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> int:
    parser = argparse.ArgumentParser(prog="email-filter", description="Gmail triage")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="poll Gmail on an interval (default)")
    run.add_argument("--once", action="store_true", help="one cycle, then exit")
    run.add_argument("--dry-run", action="store_true", help="classify but don't mutate")
    run.add_argument("--query", help="override the Gmail search query")
    run.add_argument("--interval", type=int, help="override poll interval (seconds)")
    run.add_argument("-v", "--verbose", action="store_true")

    serve = sub.add_parser("serve", help="run the FastAPI service")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--no-poller", action="store_true", help="API only, no background loop")
    serve.add_argument("--reload", action="store_true")
    serve.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()
    if args.command is None:  # bare `email-filter` behaves like `run`
        args = parser.parse_args(["run", *sys.argv[1:]])

    _configure_logging(getattr(args, "verbose", False))
    s = get_settings()

    if args.command == "serve":
        import uvicorn

        if args.host:
            s.api_host = args.host
        if args.port:
            s.api_port = args.port
        if args.no_poller:
            s.enable_background_poller = False

        uvicorn.run(
            "email_filter.api:app",
            host=s.api_host,
            port=s.api_port,
            reload=args.reload,
            log_config=None,
        )
        return 0

    # --- run ---------------------------------------------------------
    from .api import run_cycle  # imported late so `serve` isn't required for `run`

    if args.dry_run:
        s.dry_run = True
    if args.query:
        s.gmail_query = args.query
    if args.interval:
        s.poll_interval_seconds = args.interval

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if args.once:
        report = run_cycle()
        log.info("done: %d matched, %d new, %d processed",
                 report.matched, report.new, report.processed)
        return 0

    log.info(
        "polling every %ds | query: %s%s",
        s.poll_interval_seconds,
        s.gmail_query,
        " | DRY RUN" if s.dry_run else "",
    )
    while not _stop:
        try:
            run_cycle()
        except Exception:  # noqa: BLE001 -- keep the daemon alive
            log.exception("poll cycle failed; retrying next interval")

        # Sleep in slices so Ctrl-C doesn't wait out the full interval.
        for _ in range(s.poll_interval_seconds):
            if _stop:
                break
            time.sleep(1)

    log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
