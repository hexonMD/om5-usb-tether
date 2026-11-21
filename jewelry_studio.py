"""Open Jewelry Studio on a laptop, or run a shot list from an agent."""

import argparse
import json
from pathlib import Path
import sys

if __package__:
    from .jewelry_job import default_output_root, load_job, run_job, sample_job
else:
    from jewelry_job import default_output_root, load_job, run_job, sample_job


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("gui", help="Open the laptop window (default)")
    commands.add_parser("example", help="Print an editable example job as JSON")
    sample = commands.add_parser("sample", help="Process the bundled simulated focus planes and video")
    run = commands.add_parser("run", help="Process a JSON shot list using local files")
    run.add_argument("job_file", help="Path to a JSON file, or - for standard input")
    export = commands.add_parser("export-stills", help="Copy finished JPEGs to an editor's watched folder")
    export.add_argument("manifest", type=Path, help="Completed run's manifest.json")
    export.add_argument("--to", required=True, type=Path, help="Existing editor folder")
    for name, help_text in (("lightroom-sample", "Build a sample still for Lightroom Classic"),
                            ("lightroom-capture", "Release the USB shutter and download JPEGs for Lightroom Classic")):
        bridge = commands.add_parser(name, help=help_text)
        ticket = bridge.add_mutually_exclusive_group(required=True)
        ticket.add_argument("--request", type=Path, help="Plug-in request directory")
        ticket.add_argument("--ticket-hex", help="UTF-8 request directory encoded as hex by the plug-in")
    for cmd in (sample, run):
        cmd.add_argument("--output", type=Path, default=default_output_root(),
                         help="Root for SKU folders (default: Pictures/Jewelry Studio)")
    args = parser.parse_args(argv)
    try:
        if args.command in (None, "gui"):
            if __package__:
                from .jewelry_ui import launch
            else:
                from jewelry_ui import launch
            launch()
            return 0
        if args.command == "example":
            print(json.dumps(sample_job(), indent=2))
            return 0
        if args.command == "export-stills":
            if __package__:
                from .jewelry_export import export_stills
            else:
                from jewelry_export import export_stills
            print(json.dumps(export_stills(args.manifest, args.to), indent=2))
            return 0
        if args.command in ("lightroom-sample", "lightroom-capture"):
            if __package__:
                from .jewelry_lightroom_bridge import lightroom_request
            else:
                from jewelry_lightroom_bridge import lightroom_request
            ticket_path = args.request or bytes.fromhex(args.ticket_hex).decode("utf-8")
            photo = lightroom_request(ticket_path, live=args.command == "lightroom-capture")
            print(json.dumps({"status": "complete", "file": str(photo)}))
            return 0
        if args.command == "sample":
            job = sample_job()
        elif args.job_file == "-":
            job = json.load(sys.stdin)
        else:
            job = load_job(args.job_file)
        print(json.dumps(run_job(job, args.output), indent=2))
        return 0
    except Exception as error:
        print(json.dumps({"status": "error", "message": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
