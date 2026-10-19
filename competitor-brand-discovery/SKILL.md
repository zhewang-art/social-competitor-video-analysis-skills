---
name: competitor-brand-discovery
description: >-
  Given a brand name (and optionally its country/region and category), find its direct
  competitor brands via web search — returning a search-ready list of competitor brand
  names (plus a derived hashtag and a one-line rationale per competitor), not an industry
  report. Default count is 5 competitors per brand; give more when the user asks for more.
  Output is a candidate list for human confirmation before it is handed to
  social-best-video-scraper. Also triggers on "找竞品", "竞品品牌", "这个品牌的竞争对手是谁",
  "competitor brands", "who competes with X".
  NOT for: searching Instagram/Facebook and downloading videos for brands already known
  (→ social-best-video-scraper), deep competitive/positioning strategy analysis
  (→ marketing-process's competitor-benchmark mode, not present in this repo), generic
  market research reports (→ deep-research pattern, not a skill in this repo).
skill_type: primitive
---

For detailed instructions, read `INSTRUCTIONS.md` in this skill's directory.
