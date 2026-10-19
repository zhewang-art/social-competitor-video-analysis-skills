---
name: social-video-tag-analyzer
description: >-
  Analyze downloaded social videos (from social-best-video-scraper's manifest) with Gemini's
  native video understanding against a finalized tag taxonomy (story plot, selling points,
  hook type, content format, POV, RTB evidence, Hook-Problem-Solution-CTA structure, and
  more) and write a 2-tab report (results + a tag-meaning glossary rewritten every run).
  Defaults to a local Excel file — no Google auth required. Can instead write into an
  EXISTING Google Sheet the caller already has edit access to (never auto-creates one).
  Also triggers on "分析这些视频", "视频打标签", "故事线和卖点分析", "video content analysis",
  "analyze competitor videos".
  NOT for: discovering competitor brands (→ competitor-brand-discovery), searching/downloading
  videos from Instagram/Facebook (→ social-best-video-scraper), creating a brand-new Google
  Sheet from scratch (this skill never does that — see INSTRUCTIONS.md "Output format choice").
skill_type: primitive
---

For detailed instructions, read `INSTRUCTIONS.md` in this skill's directory.
