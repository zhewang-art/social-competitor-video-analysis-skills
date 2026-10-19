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

**Python packages** (not vendored — install into whatever environment runs these scripts):

```bash
pip install apify-client yt-dlp openpyxl google-genai google-auth google-api-python-client
```

(`yt-dlp` is also used as a plain CLI binary via `subprocess`, not just the Python package —
make sure the `yt-dlp` command itself is on `PATH`, e.g. `pip install -U yt-dlp` normally
provides both.)

**Credentials / access:**

- `APIFY_API_KEY` — set in a local `.env` (or your environment) for `social-best-video-scraper`.
  Uses two Apify actors (`apify/instagram-scraper`, `apify/facebook-posts-scraper`); see that
  skill's `reference/apify-actors.md` for verified field mappings and pricing. This is a paid,
  metered API — each person/team needs their own key, this pack intentionally has no shared
  key baked in anywhere.
- Gemini access for `social-video-tag-analyzer` — either Vertex AI ADC (`gcloud auth
  application-default login`) or a `GOOGLE_API_KEY` environment variable. Self-contained —
  `scripts/gemini_auth.py` handles the ADC → API-key fallback itself, no external module
  required.
- Output defaults to a local `.xlsx` file (zero extra auth beyond the above). Writing into an
  existing Google Sheet instead additionally needs a Google identity with
  `https://www.googleapis.com/auth/spreadsheets` access to that specific sheet — see that
  skill's `INSTRUCTIONS.md` "Output format choice" for the one-time setup and why it never
  auto-creates a new spreadsheet (creating one needs the much more sensitive `drive` scope,
  deliberately avoided).

**What this pack does NOT provide**: a hosted/managed version of any of this. Every person
running these skills uses their own Apify key and their own Gemini auth — there is no shared
backend service doing the scraping/analysis on anyone's behalf. If your team wants a
zero-local-setup experience (nobody installs anything, a shared service does the work), that
is a different kind of project — a small hosted API these skills call instead of running
locally — not something a skill pack by itself can provide.

## What's intentionally NOT included

Country/region filtering is a search-time **bias**, not a hard filter — neither Instagram nor
Facebook's scraping actors support a true country filter. `social-video-tag-analyzer`'s Gemini
pass makes a best-effort content-based market-match judgment instead; see that skill's
INSTRUCTIONS.md for a real example of this catching mismatched-market videos a naive
view-count ranking would have missed.
