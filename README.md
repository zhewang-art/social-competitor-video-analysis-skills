# Social Competitor Video Analysis — Skill Pack

Four Claude Code / Codex skills that together turn a brand name (or a list of brands, video
links, or account links) into a competitor video content analysis report (tag breakdown +
frequency-based correlation findings).

**Install all four together — they are not independent.** `social-video-analysis-pipeline` is
an orchestration-only skill with no processing logic of its own; it works by calling the other
three. Installing it alone will not work.

## The four skills

| Skill | Role |
|---|---|
| `social-video-analysis-pipeline` | **Start here.** Chain skill — decides which of the other three to run, in what order, based on what input you give it (a brand to find competitors for, a brand list already decided, specific video links, or account/page links). |
| `competitor-brand-discovery` | Brand name → competitor brand name list (web search, no paid APIs). |
| `social-best-video-scraper` | Brand/account/URL list → best-performing Instagram/Facebook videos, downloaded (via Apify + yt-dlp). |
| `social-video-tag-analyzer` | Downloaded videos → Gemini-based tag analysis (19 categories) + a frequency-based correlation findings table. |

## Installing

Copy all four directories into your skills folder:

```bash
# Codex
cp -R competitor-brand-discovery social-best-video-scraper social-video-tag-analyzer social-video-analysis-pipeline ~/.codex/skills/shared/

# Claude Code (project-level)
cp -R competitor-brand-discovery social-best-video-scraper social-video-tag-analyzer social-video-analysis-pipeline /path/to/your/project/.claude/skills/
```

Then just ask your assistant to analyze a brand's competitor videos — it will discover
`social-video-analysis-pipeline` and follow its decision tree.

## Requirements

- `APIFY_API_KEY` — set in a local `.env` (or your environment) for `social-best-video-scraper`.
  Uses two Apify actors (`apify/instagram-scraper`, `apify/facebook-posts-scraper`); see that
  skill's `reference/apify-actors.md` for verified field mappings and pricing.
- `yt-dlp` installed (used for video download and as a metadata fallback source).
- Gemini access for `social-video-tag-analyzer` — either Vertex AI ADC or a `GOOGLE_API_KEY`
  (see `bot/gemini_client.py`-equivalent auth in your own environment; this skill pack assumes
  a `google-genai`-compatible client is reachable the same way the rest of your setup already
  calls Gemini).
- Output defaults to a local `.xlsx` file (zero auth). Writing into an existing Google Sheet
  instead needs `google-auth`/`google-api-python-client` and a Google identity with
  `https://www.googleapis.com/auth/spreadsheets` access to that sheet — see that skill's
  `INSTRUCTIONS.md` "Output format choice" for the one-time setup and why it never
  auto-creates a new spreadsheet.

## What's intentionally NOT included

Country/region filtering is a search-time **bias**, not a hard filter — neither Instagram nor
Facebook's scraping actors support a true country filter. `social-video-tag-analyzer`'s Gemini
pass makes a best-effort content-based market-match judgment instead; see that skill's
INSTRUCTIONS.md for a real example of this catching mismatched-market videos a naive
view-count ranking would have missed.
