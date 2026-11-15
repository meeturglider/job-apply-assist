from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.config import Settings, settings
from app.service import ApplicationService


def _read_text(value: str) -> str:
    if value == "-":
        return sys.stdin.read()
    path = Path(value)
    if path.exists() and path.is_file():
        return path.read_text(encoding="utf-8")
    return value


async def _wait(service: ApplicationService, application_id: str) -> str:
    terminal = {"analyzed", "generated", "error", "needs_paste"}
    last = ""
    while True:
        record = service.get(application_id)
        if record is None:
            raise SystemExit("Application record disappeared.")
        if record.message != last:
            last = record.message
            print(f"[{record.status}] {last}", flush=True)
        if record.status in terminal:
            return record.status
        await asyncio.sleep(1.0)


async def _run(args: argparse.Namespace, active: Settings) -> int:
    service = ApplicationService(active)
    text = _read_text(args.text) if args.text else ""
    record = service.start_analysis(
        url=args.url or "",
        text=text,
        company=args.company or "",
        role=args.role or "",
    )
    print(f"Application: {record.id}")
    status = await _wait(service, record.id)
    if status != "analyzed":
        current = service.get(record.id)
        print(current.message if current else "Analysis failed.", file=sys.stderr)
        return 1
    print(f"Artifacts: {active.applications_dir / record.id}")
    if args.no_generate:
        return 0
    service.start_generation(record.id)
    status = await _wait(service, record.id)
    current = service.get(record.id)
    if status != "generated" or current is None:
        print(current.message if current else "Generation failed.", file=sys.stderr)
        return 1
    for artifact in current.artifacts:
        print(f"{artifact.kind:9} {active.applications_dir / record.id / artifact.filename}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="job-assist",
        description="Local, evidence-based ATS resume tailoring assistant.",
    )
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="Run the local web app.")
    serve.add_argument("--host", default=settings.app_host)
    serve.add_argument("--port", type=int, default=settings.app_port)
    serve.add_argument("--reload", action="store_true")

    analyze = subparsers.add_parser("analyze", help="Analyze a job description and build a resume.")
    source = analyze.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="Public job posting URL.")
    source.add_argument("--text", help="Job text, a text file path, or '-' for stdin.")
    analyze.add_argument("--company", default="")
    analyze.add_argument("--role", default="")
    analyze.add_argument("--no-generate", action="store_true", help="Only run the match analysis.")

    profile = subparsers.add_parser("profile", help="Refresh and summarize the merged profile.")
    profile.add_argument("--refresh", action="store_true")

    args = parser.parse_args(argv)
    if args.command in (None, "serve"):
        if args.command is None:
            args = parser.parse_args([*(argv or []), "serve"])
        import uvicorn

        uvicorn.run(
            "app.main:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
        return 0
    if args.command == "profile":
        from app.profile.service import ProfileService

        async def show() -> int:
            merged = await ProfileService(settings).load(refresh=args.refresh)
            print(f"{merged.identity.name} | {merged.experience_years:.1f} years")
            print(
                f"{len(merged.experience)} roles | {len(merged.projects)} projects | "
                f"{len(merged.skills)} skills | {len(merged.certifications)} certifications"
            )
            for warning in merged.warnings:
                print(f"warning: {warning}")
            return 0

        return asyncio.run(show())
    return asyncio.run(_run(args, settings))


if __name__ == "__main__":
    raise SystemExit(main())
