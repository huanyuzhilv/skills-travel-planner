# skills-travel-planner — 开源 AI 路书生成工具

面向定制游顾问与 **AI Agent（Cursor / Claude Code / Codex）** 的专业 **路书（Roadbook）生成 Skill**。  
把 txt/docx/截图里的简版行程，自动变成结构化 **`tripData.json`** 与客户可交付的 **HTML/PDF 路书**。

**关键词**：AI 路书生成、AI 行程单、定制游行路书、travel itinerary generator、roadbook generator、Agent Skill、小红书配图、LLM 文案润色。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Website](https://img.shields.io/badge/Website-skills--travel--planner.vercel.app-646cff)](https://skills-travel-planner.vercel.app)

## 特性

- **Intake**：解析简版行程表（txt/docx）→ 结构化 `tripData.json`
- **Enrich**：每日正文（小红书 + 可选 LLM）、住宿简介（飞猪 FlyAI）、亮点/费用/服务文案
- **配图**：TikHub 小红书搜索 + 飞猪/Wikimedia 兜底链，strict https 校验
- **交付**：一键 `deliver_roadbook_v2.py` 生成 roadbook-v2 HTML，可导出 PDF/长图

## 快速开始

```bash
git clone https://github.com/huanyuzhilv/skills-travel-planner.git
cd skills-travel-planner
cp .env.example .env
# 编辑 .env：填入 TIKHUB_API_KEY（https://user.tikhub.io 申请）
# 可选：OPENAI_API_KEY 或 DEEPSEEK_API_KEY（LLM 润色，见 docs/deepseek-llm-setup.md）

# 从简表生成路书目录
python3 scripts/roadbook_intake.py \
  --input docs/examples/guizhou-brief.txt \
  --output-dir generated-roadbooks/demo \
  --render --html-name demo.html

# 一键交付（须填写入住/离店日期）
python3 scripts/deliver_roadbook_v2.py \
  generated-roadbooks/demo/tripData.json \
  generated-roadbooks/demo/demo.html \
  --check-in 2026-05-01 \
  --check-out 2026-05-07
```

或使用 npm：

```bash
npm run roadbook:deliver -- tripData.json output.html --check-in YYYY-MM-DD --check-out YYYY-MM-DD
```

## 安装为 Agent Skill

```bash
./install.sh              # 交互式安装到 Claude Code / Codex
./install.sh --user       # 用户级安装
curl -fsSL https://raw.githubusercontent.com/huanyuzhilv/skills-travel-planner/main/setup.sh | bash
```

安装后 Agent 应阅读 **`AGENTS.md`**（交付禁令）与 **`skill.md`**（完整规范）。

## 环境变量

| 变量 | 说明 |
|------|------|
| `TIKHUB_API_KEY` | **推荐** — 小红书搜索/配图/正文（[TikHub](https://user.tikhub.io)） |
| `OPENAI_API_KEY` | 可选 — 每日正文与亮点 LLM 润色（优先） |
| `DEEPSEEK_API_KEY` | 可选 — DeepSeek 替代 LLM（见 `docs/deepseek-llm-setup.md`） |

密钥写入仓库根 `.env`（勿提交）；`scripts/repo_dotenv.py` 会在 deliver 时自动加载。

## 可选依赖

| 工具 | 用途 |
|------|------|
| [flyai](https://github.com/alibaba-flyai/flyai-skill) | 飞猪酒店/交通配图 |
| TikHub MCP | Cursor 内小红书搜索（见 `.cursor/mcp.json` 示例） |
| `pip install -r requirements-roadbook-images.txt` | 感知哈希去重（可选） |

## 文档

- [`AGENTS.md`](AGENTS.md) — Cursor Agent 硬性交付规则
- [`skill.md`](skill.md) — 完整 Skill 规范
- [`docs/deepseek-llm-setup.md`](docs/deepseek-llm-setup.md) — DeepSeek LLM 配置
- [`docs/roadbook-input-to-output-keywords.md`](docs/roadbook-input-to-output-keywords.md) — 检索词策略

## 可选后端集成

默认交付保留远程 **https** 配图 URL。若你有自有 CMS/CRM，可在后端将配图与品牌 Logo（`roadbook-images/logo-brand-wdtrip.png`）转存对象存储；Skill 侧仅维护 `tripData.json` 与 HTML 渲染。

## License

MIT © [huanyuzhilv](https://github.com/huanyuzhilv)
