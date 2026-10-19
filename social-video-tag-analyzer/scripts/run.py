#!/usr/bin/env python3
"""CLI entrypoint for social-video-tag-analyzer.

Usage (Excel output — default, no Google auth needed):
    uv run python .claude/skills/social-video-tag-analyzer/scripts/run.py \\
      --manifest output/social-best-video-scraper/run1/manifest.json \\
      --output-format xlsx --output-path output/analysis.xlsx

Usage (Google Sheet output — requires an existing sheet + a one-time local Google login,
see INSTRUCTIONS.md "Output format choice"):
    uv run python .claude/skills/social-video-tag-analyzer/scripts/run.py \\
      --manifest output/social-best-video-scraper/run1/manifest.json \\
      --output-format gsheet --spreadsheet-id <existing sheet id>

Reads `social-best-video-scraper`'s manifest.json, analyzes every video whose
`download_status == "ok"` with Gemini against the finalized tag taxonomy
(reference/tag_definitions.json), and writes 2 tabs (results + glossary — the glossary tab
is rewritten every run, never assumed already present) to the chosen output.

If the manifest carries a `target_country` (written by `social-best-video-scraper --target-country`),
Gemini is additionally asked to assess each video's country/market match (see
build_prompt.py "Country/market scoping") — `--target-country` on this script overrides the
manifest's value when both are given. Neither IG nor FB search supports a native country
filter, so this per-video assessment is the only point in the pipeline that can judge market
fit from the actual content.

A video with `download_status != "ok"` is reported as skipped in the output summary and
gets a row in the output noting why — never silently dropped, never analyzed via a
substitute (e.g. the thumbnail) pretending to be the real video.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from build_prompt import build_system_prompt
from frequency_analysis import build_findings
from gemini_video_analysis import analyze_video
from reason_generator import generate_reasons

DEFAULT_CONCURRENCY = 3


def _load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _flatten_videos(manifest: dict) -> list[dict]:
    """One entry per video, carrying its parent brand/platform context alongside the
    per-video result dict — flattened so the analysis step doesn't need to know about
    the manifest's brand-grouping structure."""
    flattened = []
    for brand_entry in manifest.get("brands", []):
        manifest_entry = {"brand": brand_entry.get("brand", ""), "platform": brand_entry.get("platform", "")}
        for video_result in brand_entry.get("results", []):
            flattened.append({"manifest_entry": manifest_entry, "video_result": video_result})
    return flattened


def _analyze_one(item: dict, videos_root: Path, system_prompt: str) -> dict:
    video_result = item["video_result"]
    local_file = video_result.get("local_file")
    if video_result.get("download_status") != "ok" or not local_file:
        return {**item, "analysis": None, "error": f"video not downloaded (status={video_result.get('download_status')})"}

    video_path = videos_root / local_file
    try:
        analysis = analyze_video(video_path, system_prompt)
        return {**item, "analysis": analysis, "error": None}
    except Exception as exc:
        return {**item, "analysis": None, "error": str(exc)[:500]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", required=True, help="Path to social-best-video-scraper's manifest.json")
    parser.add_argument(
        "--videos-root",
        default=None,
        help="Directory the manifest's local_file paths are relative to (default: manifest's own directory)",
    )
    parser.add_argument(
        "--output-format", choices=["xlsx", "gsheet"], default="xlsx",
        help="xlsx: local file, no Google auth needed (default). gsheet: write into an existing Google Sheet.",
    )
    parser.add_argument("--output-path", default=None, help="xlsx only: output file path (default: <manifest dir>/video_analysis.xlsx)")
    parser.add_argument("--spreadsheet-id", default=None, help="gsheet only: an EXISTING sheet's ID (required for --output-format gsheet)")
    parser.add_argument(
        "--target-country", default=None,
        help="Overrides the manifest's target_country (if any) for the Gemini country/market-match check.",
    )
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    if args.output_format == "gsheet" and not args.spreadsheet_id:
        print("ERROR: --output-format gsheet requires --spreadsheet-id (an existing sheet — this skill never auto-creates one)", file=sys.stderr)
        return 2

    videos_root = Path(args.videos_root) if args.videos_root else manifest_path.parent
    manifest = _load_manifest(manifest_path)
    items = _flatten_videos(manifest)
    if not items:
        print("ERROR: manifest contains no video results to analyze", file=sys.stderr)
        return 2

    target_country = args.target_country or manifest.get("target_country")
    system_prompt = build_system_prompt(target_country=target_country)

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futures = {pool.submit(_analyze_one, item, videos_root, system_prompt): item for item in items}
        for future in as_completed(futures):
            results.append(future.result())

    analyzed_count = sum(1 for r in results if r["analysis"] is not None)
    skipped_count = len(results) - analyzed_count

    raw_findings = build_findings(results)
    findings = generate_reasons(raw_findings, target_country=target_country)

    if args.output_format == "xlsx":
        from xlsx_output import write_xlsx_output

        output_path = Path(args.output_path) if args.output_path else manifest_path.parent / "video_analysis.xlsx"
        output_info = write_xlsx_output(results, output_path=output_path, findings=findings)
    else:
        from gsheet_output import SpreadsheetNotAccessible, write_gsheet_output

        try:
            output_info = write_gsheet_output(results, spreadsheet_id=args.spreadsheet_id, findings=findings)
        except SpreadsheetNotAccessible as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 3

    summary = {
        "status": "ok",
        "output_format": args.output_format,
        "target_country": target_country,
        "total_videos": len(results),
        "analyzed": analyzed_count,
        "skipped_or_failed": skipped_count,
        "findings_count": len(findings),
        **output_info,
        "failures": [
            {"brand": r["manifest_entry"]["brand"], "platform": r["manifest_entry"]["platform"],
             "rank": r["video_result"].get("rank"), "error": r["error"]}
            for r in results if r["analysis"] is None
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
