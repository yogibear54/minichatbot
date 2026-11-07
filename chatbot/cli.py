#!/usr/bin/env python
"""CLI: ingest | refresh | validate | serve (PLAN §3.1, §3.6, §3.2)."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from core.config import load_config
from core.pipeline import full_ingest, refresh_site

log = logging.getLogger("cli")


def cmd_ingest(cfg, args) -> int:
    manifest = full_ingest(cfg, max_pages=args.max_pages)
    _print_manifest(manifest, cfg)
    return 0 if manifest["verdict"]["status"] != "FAIL" else 2


def cmd_refresh(cfg, args) -> int:
    summary = refresh_site(cfg)
    if summary["action"] == "full":
        print("No existing bundle — ran full ingest instead.")
        _print_manifest(summary["manifest"], cfg)
        return 0 if summary["manifest"]["verdict"]["status"] != "FAIL" else 2
    print("Refresh summary:")
    for line in summary["changed"]:
        print(f"  {line}")
    print(f"  unchanged: {summary['unchanged']}/{summary['checked']} checked")
    print(f"  verdict: {summary['verdict']}  corpus: {summary['corpus_tokens']:,} tokens")
    return 0 if summary["verdict"] != "FAIL" else 2


def cmd_validate(cfg, args) -> int:
    from core import corpus, tokens

    manifest = corpus.load_manifest(cfg.data_dir)
    if manifest is None:
        print("No bundle found. Run `ingest` first.", file=sys.stderr)
        return 1
    # Recount corpus tokens from disk (guards against stale manifest numbers).
    corpus_path = cfg.data_dir / "corpus.md"
    if corpus_path.exists():
        manifest["verdict"]["corpus_tokens"] = tokens.count_tokens(corpus_path.read_text(encoding="utf-8"))
    _print_manifest(manifest, cfg)
    return 0 if manifest["verdict"]["status"] != "FAIL" else 2


def cmd_serve(cfg, args) -> int:
    import uvicorn

    from server.main import create_app

    app = create_app(cfg)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def _print_manifest(manifest, cfg) -> None:
    v = manifest["verdict"]
    print(f"\nSite:    {manifest['site_name']}  ({manifest['site_url']})")
    print(f"Model:   {manifest['model']['id']}  (context {manifest['model']['context_window']:,})")
    print(f"Pages:   {v['pages']} included, {v['skipped']} skipped, discovery={manifest.get('discovery')}")
    print(f"Corpus:  {v['corpus_tokens']:,} tokens  ({manifest.get('tokenizer', '')})")
    print("\nPer-page tokens (top 15):")
    for p in sorted(manifest["pages"], key=lambda p: -p["tokens"])[:15]:
        print(f"  {p['tokens']:>8,}  {p['url']}")
    if len(manifest["pages"]) > 15:
        print(f"  ... and {len(manifest['pages']) - 15} more")
    if manifest["skipped"]:
        print("\nSkipped:")
        for s in manifest["skipped"][:10]:
            print(f"  {s['url']} — {s['reason']}")
    icon = {"PASS": "✅", "WARN": "⚠️ ", "FAIL": "❌"}[v["status"]]
    print(f"\n{icon} Verdict: {v['status']} — {v['reason']}\n")


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(prog="chatbot", description="Markdown-only site chatbot")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=None, help="path to config.yaml (default: ./config.yaml)")
    parser.add_argument("--config", default=None, help="path to config.yaml (before the subcommand)")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text, extra in (
        ("ingest", "crawl + validate a site, build the bundle", {"--max-pages": dict(type=int, default=None)}),
        ("refresh", "incremental sync (hash diff, only changed pages)", {}),
        ("validate", "re-check the token verdict for the existing bundle", {}),
    ):
        p = sub.add_parser(name, help=help_text, parents=[common])
        for flags, kwargs in extra.items():
            p.add_argument(flags, **kwargs)

    p_srv = sub.add_parser("serve", help="run the chatbot server (API + widget)", parents=[common])
    p_srv.add_argument("--host", default="127.0.0.1")
    p_srv.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    config_path = Path(args.config or "config.yaml")
    if not config_path.exists():
        print(f"Missing {config_path}. Copy config.example.yaml to config.yaml and edit it.",
              file=sys.stderr)
        return 1
    cfg = load_config(config_path)

    return {"ingest": cmd_ingest, "refresh": cmd_refresh, "validate": cmd_validate,
            "serve": cmd_serve}[args.command](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
