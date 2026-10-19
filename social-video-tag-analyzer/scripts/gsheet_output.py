"""Write analysis results + the tag glossary into an EXISTING Google Sheet, 2 tabs every run.

Deliberately does NOT create a new spreadsheet. See INSTRUCTIONS.md "Output format choice"
for why: creating a new file needs the `drive` scope, which (a) got blocked live on
2026-10-16 by this org's Workspace policy against unverified apps requesting sensitive
scopes via a personal OAuth login, and (b) is categorically unfit for a skill meant to be
run by many different people across different machines/environments — there is no single
shared credential that's safe to distribute. `spreadsheets`-scope access to a sheet the
caller already owns or was shared is the only access model that works per-user, everywhere,
without a shared secret. Auth is plain `google.auth.default()` (matches `bot/gws_sheets.py`'s
established pattern) — each person running this skill authenticates as themselves, via
whatever ADC their own machine/environment resolves (a GCE instance SA in the anylive bot's
production environment; a personal `gcloud auth application-default login
--scopes=https://www.googleapis.com/auth/spreadsheets` one-time setup everywhere else,
documented in INSTRUCTIONS.md). A person can only write to a sheet they already have access
to — that's the correct behavior, not a limitation to work around.

Tab 1 (results) and Tab 2 (glossary) are both rewritten every run — the glossary is not a
one-time document, per the explicit business requirement: a business user opening any report
must be able to read the tag meanings without hunting down a separate reference doc.
"""

from __future__ import annotations

from typing import Any

import google.auth
from googleapiclient.discovery import build
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

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SpreadsheetNotAccessible(RuntimeError):
    """Raised when the given spreadsheet ID can't be read/written with the caller's own
    Google identity — almost always means the sheet needs to be shared with whichever
    account `google.auth.default()` resolves to."""


def _service():
    creds, _ = google.auth.default(scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def _ensure_tab(sheets, spreadsheet_id: str, tab_name: str) -> int:
    try:
        meta = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    except Exception as exc:
        raise SpreadsheetNotAccessible(
            f"Could not open spreadsheet {spreadsheet_id} with the current Google identity. "
            f"Share it with that account (Editor) and retry. Original error: {exc}"
        ) from exc
    for s in meta["sheets"]:
        if s["properties"]["title"] == tab_name:
            return s["properties"]["sheetId"]
    resp = sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
    ).execute()
    return resp["replies"][0]["addSheet"]["properties"]["sheetId"]


def _clear_and_write(sheets, spreadsheet_id: str, tab_name: str, rows: list[list[str]]) -> None:
    n_cols = max((len(r) for r in rows), default=1)
    last_col = chr(ord("A") + n_cols - 1) if n_cols <= 26 else "Z"
    sheets.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id, range=f"'{tab_name}'!A1:{last_col}10000"
    ).execute()
    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_name}'!A1",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()


def write_gsheet_output(
    results: list[dict[str, Any]], *, spreadsheet_id: str, findings: list[dict] | None = None,
) -> dict[str, Any]:
    """Write all 3 tabs into an existing spreadsheet. `results` is a list of
    {"manifest_entry", "video_result", "analysis", "error"} dicts, one per video. `findings`
    is frequency_analysis.build_findings()'s output (with `reason` filled by
    reason_generator.generate_reasons()) — omitted or empty still writes Tab 3 with just its
    header. Returns {spreadsheet_id, url}."""
    sheets = _service()

    _ensure_tab(sheets, spreadsheet_id, RESULTS_TAB)
    _ensure_tab(sheets, spreadsheet_id, GLOSSARY_TAB)
    _ensure_tab(sheets, spreadsheet_id, FINDINGS_TAB)

    result_rows = [RESULTS_HEADER] + [
        build_result_row(r["manifest_entry"], r["video_result"], r["analysis"], r["error"])
        for r in results
    ] + build_notes_rows(results)
    _clear_and_write(sheets, spreadsheet_id, RESULTS_TAB, result_rows)
    _clear_and_write(sheets, spreadsheet_id, GLOSSARY_TAB, build_glossary_rows())
    _clear_and_write(sheets, spreadsheet_id, FINDINGS_TAB, build_findings_rows(findings or []))

    return {
        "spreadsheet_id": spreadsheet_id,
        "url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
    }
