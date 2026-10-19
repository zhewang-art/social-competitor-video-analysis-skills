"""Write analysis results + the tag glossary to a local .xlsx file, 2 sheets every run.

The zero-setup output path: no Google auth of any kind, works identically on anyone's
machine regardless of ADC/service-account/Workspace-policy status — this is the right
default for a skill meant to be run by many different people across different
environments (see INSTRUCTIONS.md "Output format choice").
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.worksheet.worksheet import Worksheet
from row_builder import (
    FINDINGS_TAB,
    GLOSSARY_TAB,
    RESULTS_HEADER,
    RESULTS_TAB,
    build_findings_rows,
    build_glossary_rows,
    build_notes_rows,
    build_result_row,
)


def _write_rows(ws: Worksheet, rows: list[list[str]]) -> None:
    for row in rows:
        ws.append(row)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"


def write_xlsx_output(
    results: list[dict[str, Any]], *, output_path: Path, findings: list[dict] | None = None,
) -> dict[str, Any]:
    """Write all 3 tabs into a local .xlsx file. `results` is a list of
    {"manifest_entry", "video_result", "analysis", "error"} dicts, one per video. `findings`
    is frequency_analysis.build_findings()'s output (with `reason` filled by
    reason_generator.generate_reasons()) — omitted or empty means Tab 3 is still written with
    just its header, never skipped, so the output shape is consistent run to run.
    Returns {output_path}."""
    wb = Workbook()
    results_ws = wb.active
    results_ws.title = RESULTS_TAB
    result_rows = [RESULTS_HEADER] + [
        build_result_row(r["manifest_entry"], r["video_result"], r["analysis"], r["error"])
        for r in results
    ] + build_notes_rows(results)
    _write_rows(results_ws, result_rows)

    glossary_ws = wb.create_sheet(GLOSSARY_TAB)
    _write_rows(glossary_ws, build_glossary_rows())

    findings_ws = wb.create_sheet(FINDINGS_TAB)
    _write_rows(findings_ws, build_findings_rows(findings or []))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)

    return {"output_path": str(output_path)}
