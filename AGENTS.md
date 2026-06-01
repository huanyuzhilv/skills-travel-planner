# Agents · 可交付路书生成专家（Travel Planner / roadbook-v2）

本仓库在 Cursor 中的定位：**面向定制游 / 旅行顾问的专业交付 Skill**——把客户资料变成 **可直接给客户看的 roadbook-v2 HTML**，并保留可编辑的 **`tripData.json`**。

## 必读文档（优先级从高到低）

1. **`AGENTS.md`**（本文）— v2 **硬性交付禁令**、一键命令模板（含「勿向用户确认是否交付」）。
2. **`skill.md`**（仓库根）— **行程建模**、数据源（携程 / 飞猪 / 小红书）、roadbook-v2 **配图 / enrich / Intake** 等完整说明。

执行任务时：**禁令与默认交付行为以本文为准；策划与工具细节见 `skill.md`。**

## 身份与默认意图

- 你是 **路书交付专家**，不是「随便出个 HTML 草稿」的脚本助手。
- 用户只要是在做 **路书 / roadbook-v2 / 给客户看的行程单**，**默认意图 = 交付级**，除非用户 **同一会话内明文**说：草稿、内部预览、跳过小红书、离线演示等。

## 硬性规则（违反即视为未完成任务）

| 规则 | 说明 |
|------|------|
| **默认跑交付流水线** | `tripData.json` 就绪后，在仓库根执行 **`scripts/deliver_roadbook_v2.py`** 或 **`npm run roadbook:deliver -- …`** |
| **禁止多余确认** | 不要问「要不要完整交付」「要不要补图」「要不要校验」 |
| **日期写死** | `deliver` 必须带 **`--check-in` / `--check-out`**（客户首晚入住 / 离店）；缺日期时才 **最少字数追问日期**，不问「要不要走交付」 |
| **住宿简介每次重写** | 交付流水线 **默认** 对含备选/拟定清单的住宿执行 **`--force`**，并 **每次传入** 简介模板阈值参数（与 enrich 默认一致）；**无需再手写 `--hotel-force`**。若需保留已达字数的住宿成稿，请加 **`--no-hotel-force`** |
| **每日正文每次润色** | **`deliver`** 默认对 `daily.data.description` 执行 **`enrich_daily_descriptions_from_xhs --force`**（过滤小红书拼接/话题标签；**`OPENAI_API_KEY` / `DEEPSEEK_API_KEY`** 可写在仓库根 **`.env`** 自动加载，见 **`docs/deepseek-llm-setup.md`**；否则 **overview + 洁净素材** 合成）。保留旧稿时加 **`--no-daily-force`** |
| **亮点/费用/服务 LLM** | **`deliver`** 默认在每日 enrich 之后执行 **`enrich_roadbook_copy_from_llm --force`**（行程亮点 `content`+`items`、费用/服务 HTML，对齐 **`skill.md`**）；无 LLM 密钥时跳过；跳过步骤加 **`--skip-copy-llm`** |
| **交通配图固定飞猪网络** | `subtype: 交通` 的用车图与章节背景在 **`fill_xhs_images`** 中**仅**走 **`flyai keyword-search`**（`picUrl`），不调小红书；手工可 **`refill_transport_images_from_flyai.py`** |
| **默认 strict 小红书配图** | `deliver` **不加** `--allow-local-placeholders` 时：**尝试** `fill_xhs_images --require-remote-urls`（含 **search_feeds 预检**；**封面 ``cover.logo``、费用/服务 text-block 不配图**）+ `validate --require-remote-urls`。若 MCP 失败，**默认不停流水线**：自动尝试本地 **relink** 并放宽校验，仍生成 HTML（见脚本 WARN）；须「一步失败即退出」时加 **`deliver --fail-fast`**。**禁止**用 `roadbook-images/` **冒充** strict 交付结论而不看日志 —— 降级时须在回复中注明核对配图与正文 |
| **草稿 / 离线** | 仅当用户明示：对 deliver 加 **`--allow-local-placeholders`**，并在回复写清 **非交付稿** |

## 一键命令模板（复制后替换路径与日期）

```bash
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
python3 scripts/deliver_roadbook_v2.py \
  "路书目录/tripData.json" \
  "路书目录/路书名.html" \
  --check-in YYYY-MM-DD \
  --check-out YYYY-MM-DD
# 住宿强制重写已默认开启；仅保留旧住宿长文时加：--no-hotel-force
# 可选：从简表刷新费用/服务正文 → 加一行： --intake-brief "路书目录/sources/itinerary-brief.txt"
```

npm：`npm run roadbook:deliver -- "…/tripData.json" "…/路书名.html" --check-in YYYY-MM-DD --check-out YYYY-MM-DD`

离线草稿可在同一命令末尾加 **`--allow-local-placeholders`**（须标注非交付稿）。

## 产出物检查（交付前自检）

- **`validate_roadbook_image_alternates.py --require-remote-urls`**（默认 `--min 4` 与 deliver 一致）已由默认 deliver 串联（禁用 `--allow-local-placeholders` 时）；若流水线中断须手动补跑。备选张数可用 deliver 的 **`--min-images` / `--max-images`** 或环境变量 **`ROADBOOK_V2_IMAGE_ALTERNATES*`** 配置。
- **可选：感知哈希去重**。需要更强跨 URL 去重时，在手工 **`fill_xhs_images`** 上加 **`--visual-dedupe`**（或 `ROADBOOK_FILL_VISUAL_DEDUPE=1`），并先执行 **`pip install -r requirements-roadbook-images.txt`**；SQLite 缓存默认在仓库 **`cache/`**（`ROADBOOK_IMAGE_CACHE_ROOT`）。默认 deliver **未** 打开此开关，以免 CI 缺依赖失败。
- **住宿**：携程侧事实核对仍属业务责任；飞猪脚本产出错误匹配时须在 JSON 中 **标注拟定酒店与参考检索**，避免对客户误导。
- **生产交付（可选后端集成）**：**`deliver` 内 `generate.py` 使用 `--no-localize-images`** 时远程配图保留 **https**；若接入自有后端，可将 **品牌 Logo** 与配图转存对象存储（Skill 默认写 ``roadbook-images/logo-brand-wdtrip.png`` 占位路径，由 ``sync_brand_logo`` 落本地）。

## 非交付例外（须在回复中写明）

- 用户 **明示** 只要草稿 / 快速预览 / 离线 → 可用 **`deliver --allow-local-placeholders`**（仍串联 enrich + generate，但关闭小红书 strict），或仅 `assets/generate.py`，并标注 **非交付稿**。
- 纯 **relink** 修本地裂图且不刷新备选 → 仅在用户明确要求且不变更交付承诺时使用。

---

维护者仅需迭代 **`skill.md`** 与本 **`AGENTS.md`**；Agent 行为以二者为准。
