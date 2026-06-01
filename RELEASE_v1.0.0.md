# Release v1.0.0 — skills-travel-planner

**开源 AI 路书生成工具** 首个正式版本。

## Highlights

- Agent Skill for **Cursor / Claude Code / Codex**
- **Intake** → **Enrich** → **Images** → **Validate** → **HTML** delivery pipeline
- Structured **`tripData.json`** + client-ready **roadbook-v2 HTML/PDF**
- Xiaohongshu image enrichment (TikHub), optional LLM copywriting, FlyAI hotel data
- MIT License

## Quick Start

```bash
git clone https://github.com/huanyuzhilv/skills-travel-planner.git
cd skills-travel-planner
cp .env.example .env
# Set TIKHUB_API_KEY in .env

python3 scripts/roadbook_intake.py \
  --input docs/examples/guizhou-brief.txt \
  --output-dir generated-roadbooks/demo \
  --render --html-name demo.html

python3 scripts/deliver_roadbook_v2.py \
  generated-roadbooks/demo/tripData.json \
  generated-roadbooks/demo/demo.html \
  --check-in 2026-05-01 --check-out 2026-05-07
```

## Links

- Website: https://huanyuzhilv.github.io/skills-travel-planner/
- LLM summary: [llms.txt](https://github.com/huanyuzhilv/skills-travel-planner/blob/main/llms.txt)
- Agent rules: [AGENTS.md](https://github.com/huanyuzhilv/skills-travel-planner/blob/main/AGENTS.md)

## Search terms

AI 路书生成, roadbook generator, travel itinerary generator, Cursor skill, Claude skill, Agent skill
