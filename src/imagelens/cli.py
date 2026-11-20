"""Local image import, editable metadata, search and reviewable exports."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from .export import export_gallery, preflight, write_bytes, write_json
from .jsonutil import loads
from .library import Library
from .vision import apply_suggestion, check_model, settings_for, suggest


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--library", type=Path, help="Local library directory (required except for check-model)")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    add = sub.add_parser("add"); add.add_argument("image", type=Path)
    sub.add_parser("list")
    get = sub.add_parser("show"); get.add_argument("id")
    edit = sub.add_parser("edit"); edit.add_argument("id")
    edit.add_argument("--alt", required=True)
    edit.add_argument("--tag", action="append", default=[], dest="tags")
    edit.add_argument("--needs-review", action="store_true")
    search = sub.add_parser("search"); search.add_argument("query")
    export = sub.add_parser("export"); export.add_argument("--out", required=True, type=Path)
    original = sub.add_parser("export-original"); original.add_argument("id")
    original.add_argument("--out", required=True, type=Path)
    gallery = sub.add_parser("gallery"); gallery.add_argument("--out", required=True, type=Path)
    gallery.add_argument("--query")
    for name in ("suggest", "check-model"):
        command = sub.add_parser(name, help="Optional local vision model integration; requires installed vision capability")
        command.add_argument("--model", required=True)
        command.add_argument("--base-url", default="http://127.0.0.1:11434")
        command.add_argument("--timeout", type=float, default=90)
        if name == "suggest":
            command.add_argument("id")
            command.add_argument("--out", required=True, type=Path)
    apply = sub.add_parser("apply", help="Apply a vision draft as unreviewed metadata")
    apply.add_argument("draft", type=Path)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if hasattr(args, "out"):
            preflight(args.out)
        if args.command == "check-model":
            result = check_model(settings_for(args.model, args.base_url, args.timeout))
        else:
            if args.library is None:
                raise ValueError("--library is required for this command")
            library = Library(args.library)
            if args.command == "init":
                result = {"library": str(library.directory)}
            elif args.command == "add": result = library.add(args.image)
            elif args.command == "list": result = library.list()
            elif args.command == "show": result = library.get(args.id)
            elif args.command == "edit": result = library.edit(args.id, args.alt, args.tags, reviewed=not args.needs_review)
            elif args.command == "search": result = library.search(args.query)
            elif args.command == "export":
                records = library.list()
                write_json(args.out, {"schema_version": 1, "images": records})
                result = {"images": len(records), "output": str(args.out)}
            elif args.command == "export-original":
                write_bytes(args.out, library.read_original(args.id)); result = {"output": str(args.out)}
            elif args.command == "gallery": result = export_gallery(library, args.out, args.query)
            elif args.command == "suggest":
                draft = suggest(library, args.id, settings_for(args.model, args.base_url, args.timeout))
                write_json(args.out, draft); result = {"draft": str(args.out), "status": "needs_review"}
            else:
                with args.draft.open("rb") as stream: raw = stream.read(65_537)
                if len(raw) > 65_536: raise ValueError("Vision draft exceeds 64 KiB")
                result = apply_suggestion(library, loads(raw))
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, sqlite3.Error, RecursionError) as exc:
        print(f"imagelens: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
