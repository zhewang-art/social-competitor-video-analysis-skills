---
name: social-best-video-scraper
description: >-
  Given a list of brand/competitor names (optionally with hashtags), search Instagram and
  Facebook via Apify and return each brand's best-performing videos (ranked by view count,
  or an engagement-weighted score on Facebook when views are not exposed), download the
  actual video files, and write a manifest that records brand/platform/rank/metric/url/local
  path for downstream consumption. Also triggers on "扒竞品视频", "best performing videos",
  "IG/FB 爬最佳视频", "scrape top videos", "社媒最佳视频下载".
  NOT for: discovering the competitor brand list itself (→ competitor-brand-discovery),
  analyzing video content/story/selling points (→ a future video-analysis skill, not yet
  built), designing or syncing AI livestream schedules (→ schedule-design / schedule-to-calendar).
skill_type: primitive
---

For detailed instructions, read `INSTRUCTIONS.md` in this skill's directory.
