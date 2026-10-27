"""Command-line entry point: scrape, notify, run, and openapi."""

from __future__ import annotations

import argparse
import json
import sys

import httpx

from ptjobs.config import get_settings
from ptjobs.db import session_scope
from ptjobs.models import SOURCES
from ptjobs.notify import alert_admin, build_deliver, run_digest
from ptjobs.pipeline import run_source
from ptjobs.sources import itjobs, linkedin, net_empregos
from ptjobs.sources.http import HttpClient

LINKEDIN_KEYWORDS = [
    "python developer",
    "backend engineer",
    "data engineer",
    "devops engineer",
    "frontend developer",
]
CRAWL_DELAY_SECONDS = 1.0


def _scrapers(client: httpx.Client) -> dict[str, object]:
    polite = HttpClient(client, min_interval=CRAWL_DELAY_SECONDS)
    return {
        "net_empregos": lambda: net_empregos.fetch(polite),
        "itjobs": lambda: itjobs.fetch(polite),
        "linkedin": lambda: linkedin.fetch(LINKEDIN_KEYWORDS),
    }


def run_scrape(sources: list[str] | None = None) -> None:
    """Scrape the given sources (all by default), committing each one separately."""
    settings = get_settings()
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        scrapers = _scrapers(client)
        for name in sources or list(scrapers):
            with session_scope() as session:
                run_source(
                    name,
                    scrapers[name],  # type: ignore[arg-type]
                    session=session,
                    alert=lambda message: alert_admin(settings, client, message),
                )


def run_notify() -> None:
    """Deliver digests of new matching jobs to verified subscriptions."""
    settings = get_settings()
    with httpx.Client(timeout=20.0) as client:
        deliver = build_deliver(settings, client)
        with session_scope() as session:
            run_digest(
                session,
                deliver=deliver,
                jobs_url=f"{settings.public_base_url}/jobs",
                base_url=settings.public_base_url,
            )


def _cmd_openapi(_: argparse.Namespace) -> int:
    from ptjobs.api import create_app

    sys.stdout.write(json.dumps(create_app().openapi(), indent=2, sort_keys=True))
    sys.stdout.write("\n")
    return 0


def _cmd_scrape(args: argparse.Namespace) -> int:
    run_scrape([args.source] if args.source else None)
    return 0


def _cmd_notify(_: argparse.Namespace) -> int:
    run_notify()
    return 0


def _cmd_run(_: argparse.Namespace) -> int:
    run_scrape()
    run_notify()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch to the selected command."""
    parser = argparse.ArgumentParser(prog="ptjobs")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("openapi", help="Print the OpenAPI schema to stdout")
    scrape = sub.add_parser("scrape", help="Scrape sources into the database")
    scrape.add_argument("--source", choices=SOURCES, help="Limit to one source")
    sub.add_parser("notify", help="Deliver digests to verified subscriptions")
    sub.add_parser("run", help="Scrape all sources then deliver digests")

    args = parser.parse_args(argv)
    handlers = {
        "openapi": _cmd_openapi,
        "scrape": _cmd_scrape,
        "notify": _cmd_notify,
        "run": _cmd_run,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
