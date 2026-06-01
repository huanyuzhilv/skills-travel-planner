# CLAUDE.md

此文件为 Claude Code (claude.ai/code) 在本仓库中工作时提供指导。

## 项目概述

Travel Planner Skill —— 面向定制游服务商的专业路书生成系统。将简版行程表（文本、docx、截图）转化为可直接交付客户的 HTML/PDF 路书，底层为结构化的 `tripData.json`。输出为中文。

**权威文档（在本仓库工作时优先阅读）：**
- `AGENTS.md` — 交付禁令、一键命令模板、硬性规则（如"禁止向用户确认是否跑交付流水线"）
- `skill.md` — 完整流程规范：旅行策划阶段、数据源规则、配图优先级、Intake 格式、LLM 提示词模板

## 交付流水线（核心工作流）

标准交付命令按固定顺序执行 7 个步骤：

```bash
python3 scripts/deliver_roadbook_v2.py \
  "路书目录/tripData.json" \
  "路书目录/路书名.html" \
  --check-in YYYY-MM-DD --check-out YYYY-MM-DD
```

或通过 npm：`npm run roadbook:deliver -- "…/tripData.json" "…/路书名.html" --check-in YYYY-MM-DD --check-out YYYY-MM-DD`

步骤（按顺序执行）：
1. `merge_intake_fee_service.py` — 从简表合并费用/服务文本（仅当传入 `--intake-brief` 时）
2. `enrich_daily_descriptions_from_xhs.py` — 每日行程正文润色（小红书 → 飞猪 POI → 维基兜底，可选 LLM 润色）
3. `enrich_hotel_intro_from_flyai.py` — 住宿长简介（飞猪 FlyAI）
4. `sync_brand_logo.py` — 品牌 Logo 复制到 roadbook-images/
5. `fill_xhs_images.py` — 按槽搜图（小红书 → 飞猪 → Wikimedia → 占位图兜底链）
6. `validate_roadbook_image_alternates.py` — 校验每槽 ≥ N 张 https URL
7. `assets/generate.py` — 渲染 HTML

关键参数：`--allow-local-placeholders`（草稿模式）、`--fail-fast`（遇错即停）、`--no-hotel-force` / `--no-daily-force`（跳过强制重写）、`--skip-daily-enrich`、`--min-images` / `--max-images`（默认 4）。

退出码：`0` = strict 交付成功；`2` = 已降级（某步骤使用了本地回退——不可直接交付客户，需人工核对）。

## 快速录入：简版行程 → tripData.json

```bash
python3 scripts/roadbook_intake.py \
  --input docs/examples/guizhou-brief.txt \
  --output-dir generated-roadbooks/贵州黔南环线-2026-05 \
  --render --html-name 贵州黔南6天路书.html
```

## 核心脚本一览

| 脚本 | 用途 |
|------|------|
| `scripts/roadbook_intake.py` | 解析简版行程（txt/docx）→ `tripData.json` |
| `scripts/deliver_roadbook_v2.py` | 完整交付流水线编排 |
| `scripts/enrich_daily_descriptions_from_xhs.py` | 每日行程正文润色（小红书 + LLM 润色） |
| `scripts/enrich_hotel_intro_from_flyai.py` | 住宿简介（飞猪 FlyAI） |
| `scripts/fill_xhs_images.py` | 按槽搜图（TikHub API） |
| `scripts/tikhub_xhs_cache.py` | 共享 TikHub `search_notes` / `get_note_info` 结果，避免 enrich 与 fill 重复扣费（`sources/xhs-note-cache.json`，默认 TTL 7 天，环境 `ROADBOOK_XHS_CACHE_TTL_SEC`） |
| `scripts/tikhub_xhs_search.py` | TikHub 关键词搜笔记（标题/正文/图片 URL，CLI） |
| `scripts/tikhub_xhs_client.py` | TikHub 小红书 REST 客户端 |
| `scripts/validate_roadbook_image_alternates.py` | 校验每槽图片 URL 数量 |
| `scripts/image_fallback_chain.py` | 图片兜底链：小红书 → 飞猪 → Wikimedia → 占位图 |
| `scripts/merge_intake_fee_service.py` | 费用/服务文本合并到 text-block 组件 |
| `scripts/xhs_search_keyword_rules.py` | 图片搜索关键词构造规则 |
| `scripts/xhs_image_url_rules.py` | URL 指纹、去重、校验 |
| `scripts/relink_local_roadbook_images.py` | 修复断裂的本地图片引用 |
| `scripts/refill_transport_images_from_flyai.py` | 交通配图（仅走飞猪，不走小红书） |
| `scripts/sync_brand_logo.py` | 品牌 Logo 复制到 roadbook-images/ |
| `scripts/roadbook_image_engine/` | 感知哈希去重（`visual_hash.py`）、质量分、缓存存储 |
| `assets/generate.py` | `tripData.json` → HTML 渲染（模板来自 `assets/templates/`） |

## 数据采集优先级（硬约束）

- **景点/美食/玩法图片**：小红书 → FlyAI → Grok 搜索 → Wikimedia（前序命中即停，禁止跳级）
- **酒店相关信息**（列表、价格、评分、设施、图片）：**仅允许 FlyAI / 携程**。小红书可用于体验补充，但不可作为酒店主数据源。
- **交通配图**：仅走 FlyAI keyword-search（不走小红书）
- 若 FlyAI 与携程均无结果，须标注"酒店数据缺失"，不得用其他来源凑数。

## 环境变量与配置

- `.env` 文件位于仓库根目录（由 `deliver_roadbook_v2.py` 和 `enrich_daily_descriptions_from_xhs.py` 通过 `scripts/repo_dotenv.py` 自动加载）：
  - `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` — 每日正文 LLM 润色（优先）
  - `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` — 备选 LLM（未设 OpenAI 时生效）
  - 详见 `docs/deepseek-llm-setup.md`
- 运行时控制：`ROADBOOK_FILL_XHS_COOLDOWN_MS`、`ROADBOOK_V2_IMAGE_ALTERNATES`、`ROADBOOK_IMAGE_CACHE_ROOT`（默认 `cache/`）、`ROADBOOK_XHS_AUTO_LOGIN`、`ROADBOOK_FILL_VISUAL_DEDUPE`、`ROADBOOK_XHS_CACHE_TTL_SEC`（默认 7 天；置 0 表示不过期）
- 图片去重依赖：`pip install -r requirements-roadbook-images.txt`

## 外部 CLI 依赖（均为可选，有 web-search 兜底）

- `flyai` (npm) — 飞猪实时数据（机票、酒店、门票）
- **TikHub API** — 小红书搜索/配图/正文（`.env` 中 `TIKHUB_API_KEY`）；CLI：`scripts/tikhub_xhs_search.py`
- `mcp__grok-search__web_search` — 通用网络搜索兜底

## 模板

- `assets/templates/roadbook-v2/template-roadbook-v2.html` — 主力生产模板
- `assets/templates/default/`、`assets/templates/roadbook/`、`assets/templates/shared/` — 旧版/兜底模板
- `assets/brand/wd-trip-logo.png` — 默认品牌 Logo

## 产出目录结构

每本路书生成在 `generated-roadbooks/<名称>/` 下：
- `tripData.json` — 标准化结构化数据（可编辑）
- `<名称>.html` — 渲染后的路书
- `roadbook-images/` — 本地化图片（与 HTML 相对路径同级）
- `sources/` — 原始输入简表
