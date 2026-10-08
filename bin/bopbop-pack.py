#!/usr/bin/env python3
"""Pack validation, tool discovery/calls, and stdio MCP entry point."""
import argparse
import json
from pathlib import Path
import sys

from bopbop_packs import Pack, Registry, context_directory, serve_mcp, strict_json, wrapper_options


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context-dir", type=Path, default=context_directory())
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("path", type=Path)
    listing = sub.add_parser("tools")
    listing.add_argument("packs", nargs="*")
    call = sub.add_parser("call")
    call.add_argument("pack")
    call.add_argument("tool")
    call.add_argument("arguments", nargs="?", default="{}")
    mcp = sub.add_parser("mcp")
    mcp.add_argument("--packs", required=True)
    wrapper = sub.add_parser('wrapper')
    wrapper.add_argument('--harness', choices=('claude','codex'), required=True)
    wrapper.add_argument('--profile', required=True)
    wrapper.add_argument('args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.command == "validate":
            pack = Pack(args.path)
            print(json.dumps({"name": pack.name, "profile": pack.profile,
                              "tools": [d["name"] for d in pack.definitions]}))
        elif args.command == "mcp":
            serve_mcp(Registry(args.context_dir, args.packs.split(",")))
        elif args.command == 'wrapper':
            import os
            binary = os.environ.get(f'BOPBOP_{args.harness.upper()}_BIN',args.harness)
            tail = args.args[1:] if args.args[:1] == ['--'] else args.args
            options = wrapper_options(args.harness,args.context_dir,args.profile)
            os.chdir(args.context_dir)
            os.execvp(binary,[binary,*options,*tail])
        elif args.command == "call":
            value = Registry(args.context_dir, [args.pack]).call(args.tool, strict_json(args.arguments))
            print(json.dumps(value, ensure_ascii=False))
            return 1 if value.get("ok") is False else 0
        else:
            selected = args.packs or [p.parent.name for p in sorted((args.context_dir / "packs").glob("*/pack.toml"))]
            print(json.dumps(Registry(args.context_dir, selected).schemas, ensure_ascii=False, indent=2))
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f"bopbop pack: {exc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
