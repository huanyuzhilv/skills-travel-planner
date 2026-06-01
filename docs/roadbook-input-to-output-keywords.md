# 路书：输入 → 输出与关键词策略

本文档说明 **roadbook-v2** 从客户资料到 `tripData.json` / HTML 的流程中：**输入如何识别**、**各模块关键词如何生成**、**费用/服务**如何处理。与仓库实现一致，便于交付与二次开发。

---

## 1. 输入识别（Intake）

| 输入形态 | 仓库内做法 | 说明 |
|----------|------------|------|
| **纯文本**（聊天 / `.txt` / `.md`） | `scripts/roadbook_intake.py` 或人工维护 `tripData.json` | 直接解析行程骨架、节点、报价段落等。 |
| **截图 / 图片** | 流程约定见 `skill.md` | **先 OCR**，再**人工核对**关键字段；仓库脚本不内置通用 OCR。 |
| **文档**（`.docx` / `.pdf` 等） | `roadbook_intake.py --input …` | 先抽**文字主干**；图片多作**搜图关键词提示**，结构化仍以文字为准。 |

**可选：简表合并费用/服务**  
`scripts/deliver_roadbook_v2.py` 传入 `--intake-brief "…/itinerary-brief.txt"` 时，会先执行 `scripts/merge_intake_fee_service.py`，将简表中识别到的**费用 / 服务**段落写入 `text-block`（`subtype: 费用` / `服务`）的 `content`（HTML），规则与 `roadbook_intake` 中章节关键词一致。

---

## 2. 交付主链路（默认）

`scripts/deliver_roadbook_v2.py`（非 `--allow-local-placeholders` 草稿）大致顺序：

0. **（可选）** `merge_intake_fee_service.py` — 简表 → 费用/服务正文  
1. `enrich_daily_descriptions_from_xhs.py` — 每日 `daily.data.description`（小红书/飞猪 POI/维基作**素材**；可选 LLM 润色）  
2. `enrich_hotel_intro_from_flyai.py` — 住宿卡片**文字简介**（飞猪 `search-hotels`，维基兜底）  
3. `sync_brand_logo.py` — 封面品牌 Logo  
4. `fill_xhs_images.py` — **配图**（小红书为主；**交通固定飞猪网络**；strict 时 https + 兜底链）  
5. `validate_roadbook_image_alternates.py`  
6. `assets/generate.py` — 生成 HTML；默认 `--save-updated-json` 将配图本地化并回写 `tripData.json`

---

## 3. 配图关键词总闸

实现位置：

- **槽位分类**：`scripts/xhs_search_keyword_rules.py` → `classify_image_slot`
- **关键词列表**：同文件 → `planned_search_keywords`
- **执行搜图**：`scripts/fill_xhs_images.py`（小红书 MCP：`search_feeds` / `get_feed_detail`）

**通用约束**：单条关键词建议 **≤24 字符**（常量 `_MAX_KEYWORD_CHARS`），过长会截断或拆分，避免小红书召回变差。

---

## 4. 封面 / 大章节背景图（标题感、目的地风光）

| 项目 | 说明 |
|------|------|
| **槽位类型** | `cover_bg`（封面底图）；行程亮点、行程概览等章节顶图多为 `section_hero`；部分 `feature` 的 `backgroundImage` 为 `feature_section_bg`（**交通 subtype 除外**，见下文）。 |
| **生成函数** | `hero_landmark_keywords(data, slot_label)` |
| **主干来源** | `primary_destination_core(data)`：从 `meta.title` / `cover.title` 抽取短目的地短语；再结合该槽 **`slotLabel`**（过长时 `_trim_destination` 只取首段）。 |
| **模板示例** | `{目的地} 风光 大气`、`{目的地} 航拍 风景`；必要时 `{core} {lab} 航拍 风光`。 |
| **分桶扩展** | `_keyword_bucket_extensions`：对 `cover_bg` / `section_hero` / `feature_section_bg` 追加如 **航拍 / 实拍 / 夜景** 等与 seed 的组合。 |
| **图片来源** | 默认 **小红书**（`fill_xhs_images.py`）。 |

---

## 5. 每日行程相关配图

| 项目 | 说明 |
|------|------|
| **槽位类型** | `daily_activity`（`daily` 的 `backgroundImage`、`sideImage`、`topImages`、`bottomImages` 等）。 |
| **生成函数** | `daily_play_keywords(data, path, slot_label)` |
| **主干** | 优先 **`daily.data.theme`**：按 **`·` / `•` 切段** 得到多个 spot（如「堂安梯田」「肇兴侗寨」）。若无 theme，则用 **`slotLabel`** 推演（如「西湖」）。 |
| **后缀轮换** | `_DAILY_SUFFIX_GROUPS` 三组轮换，例如：`攻略/风景/描述`、`游记/实拍/打卡`、`玩法/路线/小众`；并按 **当日在全书 daily 中的序号** 轮换 **切段顺序** 与 **后缀组**，降低多日笔记撞车。 |
| **整段主题** | `_condensed_theme_base(theme_line)` 与切段一并进入关键词矩阵。 |
| **与全书目的地组合** | 若有 `primary_destination_core`，可追加如 **`{core} {当日首段} 风景`** 等。 |
| **分桶扩展** | `daily_activity`：**实拍 / 游客 / 航拍 / 栈道** 等与 seed 组合。 |
| **图片来源** | 默认 **小红书**。 |

**关于「时段词」（如黄昏、日落）**  
当前规则**未单独内置**「黄昏」模板；若希望强绑定，可把词写进 **`theme` 或 `slotLabel`**（注意 24 字限制），或后续扩展 `xhs_search_keyword_rules.py`。

---

## 6. 酒店相关（简介 vs 配图）

| 维度 | 脚本 / 逻辑 | 说明 |
|------|-------------|------|
| **住宿简介（正文）** | `enrich_hotel_intro_from_flyai.py` | **飞猪 `search-hotels`**：按酒店名、入离日期、城市别名等拉字段，拼成长简介；飞猪不足时 **中文维基** 兜底。 |
| **住宿配图（默认）** | `hotel_gallery_keywords(slotLabel)` | 从 `slotLabel` 按空格切词，遇 **外观/大堂/客房/房型/…** 等限定词截断，保留**酒店主名**，再拼 **`{主名} 酒店 实拍`**、大堂外观、客房房型等 → **小红书搜图**。 |
| **配图不足** | `image_fallback_chain.collect_fallback_urls` | `hotel_gallery` 可走 **飞猪酒店 `mainPic`** 等补足 https（与 `fill_xhs_images` 兜底链一致）。 |

**小结**：**酒店「长文信息」偏飞猪；默认搜图以小红书关键词为主，飞猪可作补图兜底。**

---

## 7. 交通相关（固定飞猪网络）

| 项目 | 说明 |
|------|------|
| **槽位** | 用车图：`transport_gallery`（`subtype: 交通` 的 `items[].images`）；交通章节顶栏：`subtype: 交通` 的 `data.backgroundImage`。 |
| **实现** | `fill_xhs_images.py`：**不调小红书**，直接 **`flyai keyword-search`** 取商品 **`picUrl`**；逻辑与查询词见 `scripts/image_fallback_chain.py`（如 `flyai_transport_mainpics`、`flyai_transport_section_bg_urls`、`transport_flyai_keyword_queries`）。 |
| **手工重刷** | `scripts/refill_transport_images_from_flyai.py` | 仅更新交通条目的 `images`，需再跑 `assets/generate.py` 同步 HTML。 |

---

## 8. 费用说明 / 服务说明

| 项目 | 说明 |
|------|------|
| **内容来源** | `roadbook_intake.py`：用章节关键词（如「报价包含」「费用不含」「服务说明」等）从简表拆段，生成 **`text-block`** 的 **`content`（HTML）**，`subtype` 为 **费用** 或 **服务**。 |
| **交付合并** | `deliver_roadbook_v2.py` + `--intake-brief` → `merge_intake_fee_service.py` 写入/合并现有 `tripData`。 |
| **配图** | `fill_xhs_images.py`：**费用 / 服务 / 须知** 的章节配图槽 **不配小红书**（清空或跳过），与模板白板一致。 |
| **关键词** | 费用/服务**不参与**小红书搜图；本质是 **结构化 HTML 正文**，不是配图关键词。 |
| **客户可见正文** | `content` 只写**成团、保险、装备、礼仪、安全、退改**等；**不要**写「导出 HTML/PDF」「修改 JSON 重新生成」「配图位」等**对内编辑/流水线说明**（易由 Agent 误写入，不属于客户路书）。 |

---

## 9. 每日「描述」文案（非配图）

| 项目 | 说明 |
|------|------|
| **脚本** | `enrich_daily_descriptions_from_xhs.py` |
| **拉素材关键词** | `daily_enrich_description_keywords(data, theme, daily_ordinal=…)` — 与每日配图类似的 **theme 切段 + 日序轮换后缀**，并可含 **`{core} {段} 攻略/风景`**。 |
| **写入字段** | 仅 **`daily.data.description`**（及成功更新时的 `meta.updatedAt`）。 |
| **LLM** | 可选 **`OPENAI_API_KEY`** + **`OPENAI_BASE_URL`** + **`OPENAI_MODEL`**，或仅 **`DEEPSEEK_API_KEY`**（默认 `https://api.deepseek.com/v1` + `deepseek-chat`）。详见 **`docs/deepseek-llm-setup.md`**。提示词见脚本内 `DAILY_DESCRIPTION_LLM_*`。未配置时走 **overview + 洁净素材** 顾问合成。 |

---

## 10. 与产品表述的对照

1. **输入识别** — 见本文 §1；细节以 `skill.md` Intake 为准。  
2. **标题背景图** — 以 **目的地核心词 + slotLabel 截断 + 风光/航拍/夜景分桶** 走小红书，见 §4。  
3. **每日行程图** — **theme 切段 + slotLabel + 轮换后缀 + 实拍/游客等分桶** 走小红书，见 §5。  
4. **酒店** — **简介：飞猪**；**配图：默认小红书关键词 + 飞猪补图兜底**，见 §6。  
5. **费用/服务** — **Intake/简表整理为 HTML**；**不配搜图**；可选 **`--intake-brief`**，见 §8。

---

## 11. 相关文件速查

| 文件 | 作用 |
|------|------|
| `scripts/xhs_search_keyword_rules.py` | 槽位分类、小红书检索词生成 |
| `scripts/fill_xhs_images.py` | 预检、按槽搜图、交通走飞猪、费用/服务跳过配图 |
| `scripts/image_fallback_chain.py` | 飞猪 keyword-search（交通）、酒店/POI/Commons/占位兜底 |
| `scripts/repo_dotenv.py` | 从仓库根 `.env` 加载环境变量（`deliver` / `enrich_daily` 入口调用） |
| `scripts/enrich_hotel_intro_from_flyai.py` | 住宿长简介（飞猪） |
| `scripts/roadbook_intake.py` | 简表 → `tripData` 骨架与费用/服务段落 |
| `scripts/merge_intake_fee_service.py` | 简表合并进已有路书 |
| `scripts/deliver_roadbook_v2.py` | 一键交付顺序 |
| `scripts/refill_transport_images_from_flyai.py` | 仅重刷交通用车图（飞猪） |
| `skill.md` | Intake、交付约定、Agent 行为 |
| `docs/deepseek-llm-setup.md` | DeepSeek / OpenAI 兼容：每日正文 LLM 环境变量 |
| `docs/roadbook-input-to-output-keywords.md` | 输入→输出与配图关键词总览 |**配图词库改 `xhs_search_keyword_rules.py`**；**交通飞猪查询改 `image_fallback_chain.py`**；**流程顺序改 `deliver_roadbook_v2.py`**。
