---
name: social-video-analysis-pipeline
description: >-
  Chain skill: orchestrates competitor-brand-discovery → social-best-video-scraper →
  social-video-tag-analyzer end-to-end, choosing the right entry point based on what the user
  actually provided — a brand that needs its competitors discovered, a brand/competitor list
  already decided, specific video links, or specific account/page links (any mix, per brand).
  Owns no domain processing itself — see INSTRUCTIONS.md for the decision tree before running
  anything. Also triggers on "分析这个品牌的竞品视频", "帮我做竞品视频分析全流程", "从品牌名到
  视频分析报告", "run the full competitor video analysis pipeline", "analyze these videos end
  to end".
  NOT for: running just one stage in isolation — call that primitive directly instead
  (competitor-brand-discovery / social-best-video-scraper / social-video-tag-analyzer);
  redefining the tag taxonomy, correlation methodology, or actor/data-source choices (all
  already finalized inside the primitives this skill orchestrates, never duplicated here).
skill_type: chain
---

For the full decision tree, step sequencing, and handoff rules, read `INSTRUCTIONS.md` in
this skill's directory before running anything.
