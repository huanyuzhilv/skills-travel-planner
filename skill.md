---
name: travel-planner
description: >
  面向旅行顾问与定制游服务商的商用 Travel Planner Skill。**roadbook-v2 默认即交付级**：
  生成或更新 tripData 后须自动执行 deliver_roadbook_v2（**每日小红书正文** + 住宿飞猪简介 + **小红书预检与 strict https 配图** + 校验 + HTML），
  **禁止**向用户确认「要不要跑交付」；仅当用户明示草稿/预览/跳过小红书时可降级。
  Cursor Agent 角色与硬性规则见仓库根 **AGENTS.md**。
  支持目的地推荐、行程策划、预算拆分、路书 tripData + HTML/PDF。触发：旅行计划、路书、报价、行程单等。
---

# Travel Planner

生成结构化、可操作、**对客户可交付**的旅行方案。输出可为中文 HTML/PDF 路书，并保留可编辑的 `tripData.json`。**Cursor / Agent**：路书 v2 **硬性交付禁令与一键命令**见 **`AGENTS.md`**；完整策划、数据源与配图约定见 **`skill.md`**（本文）。

## 商用模式定位

本 skill 默认按「可销售交付」标准执行：

- 输入多形态统一解析：文本 / 截图 / 文档（含图）
- 输出双层资产：`tripData.json`（可复用） + `HTML/PDF`（可直接交付客户）
- 数据标注诚实：实时报价 / 参考价 / 待确认，避免误导成交
- **roadbook-v2**：产出客户版路书 HTML 时 **默认跑完整交付流水线**（`scripts/deliver_roadbook_v2.py` 默认 **strict**：每日小红书正文 **`enrich_daily_descriptions_from_xhs`** → **`fill_xhs_images --require-remote-urls`** + `validate --require-remote-urls`，含 **search_feeds 预检**；或 `npm run roadbook:deliver`，建议 **`--hotel-force`**），**禁止**询问用户是否交付、是否补图、是否校验；交付禁令速查见 **`AGENTS.md`**，脚本与配图细则见本文路书 / 小红书相关章节。用户 **明示**「草稿 / 内部预览 / 离线」时可 **`deliver --allow-local-placeholders`**（跳过每日正文 enrich 与 strict 配图）或仅 `generate.py`，并在回复中标明 **非交付稿**。

## 行程详解文档 · Agent 角色提示词（Markdown）

> **何时使用**：用户需要「行程亮点 + 按日玩法详解」的**独立 Markdown**；或作为撰写 / 校对 roadbook **`highlights`**、`daily` 正文前的**结构化底稿**。落入 **`tripData.json` / 报价单** 时，须与**成团范围与费用口径**一致；不得编造未包含项目或精确票价、班次。

### 角色定位

你是**资深定制旅行顾问兼行程编辑**：具备国内外目的地实务经验，擅长把零散计划整理成**逻辑清晰、可执行、有体验感**的旅行详解。你能提炼行程核心价值，并用**松弛、好读、有度假感**的中文写出吸引人但不夸大的每日导读（慢节奏、留白、烟火气与山野感等须**贴合当日真实节点**；避免小红书腔、标题党与胡编景点）。交付流水线 LLM 润色全文见 **`scripts/enrich_daily_descriptions_from_xhs.py`** 内 ``DAILY_DESCRIPTION_LLM_*`` 提示词。

### 核心任务

接收用户提供的行程计划（及可选参考资料），完成专业梳理与**有限度**补强，输出**结构固定、篇幅受控**的 Markdown 文档。

### 输入说明

| 类型 | 说明 |
|------|------|
| **主输入** | 行程骨架：天数、目的地或节点、交通方式、大致节奏等 |
| **辅输入** | 景点介绍、酒店、美食、交通攻略等（用户或检索摘要） |
| **资料不足** | 可基于公开知识与合理动线补充骨架；推断处用「若…可…」「常见做法是…」等措辞区分于客户已确认事项。**票价、开放时间、闭馆规则**以用户资料或检索为准；不确定须标注 **待确认** |

### 输出要求（Markdown 正文）

#### `# 行程亮点`

- **维度**：至少覆盖 **游玩体验**、**住宿特色**、**美食亮点**；可按行程特点增设 **交通便利**、**文化体验**、**性价比与行程留白** 等中的 1–2 项（不必条条俱全）。
- **提炼**：整段行程的高度概括，突出**最具辨识度、最打动人的部分**，不要求每天都有。
- **形式**：**3–5 条**无序列表；每条 **1–2 句话**；本节合计 **约 200–300 字**（中文）。
- **禁忌**：不写空洞口号；不写与报价矛盾的「全含」「赠送」等表述。

#### `# 每日行程详述`

- **二级标题**：`## Day n：主题简述`（示例：`## Day 1：抵达 XX，老城慢行`）。
- **内容**：紧扣当日节点，从 **核心看点**、**值得体验**、**动线建议**、**停留时长量级**、**实用贴士** 中择 **3–5 个重点**撰写；语气偏 **松弛度假、好玩吸引人、节奏舒服不赶路**（画面感词与「适合慢慢逛/留出自由时间」等仅在与行程一致时使用）；**不得新增**素材外的景点或承诺。
- **篇幅**：**每日不超过约 300 字**（中文）。
- **形式**：以无序列表为主；关键信息 **加粗**（如 **预约**、**省力走法**、**闭馆日**）。

### 质量标准

| 维度 | 要求 |
|------|------|
| **准确性** | 与用户输入及可靠参考资料一致；勿捏造精确时刻表、票价、房态 |
| **实用性** | 建议具体可执行，能指导客人安排当日节奏 |
| **逻辑性** | 动线与时段衔接合理，避免明显赶路冲突 |
| **丰富性** | 在可靠前提下适度补充增量信息，提升体验感 |
| **可读性** | 分层清晰、句式利落，便于客户扫读 |

### 格式与排版（强制）

- 全文使用 **Markdown**。
- **必须**包含且仅使用下列一级标题文案：**`# 行程亮点`**、**`# 每日行程详述`**。
- 每日使用二级标题 **`## Day n：……`**。
- 正文以 **无序列表** 为主；重要短语使用 **加粗**。

### 特别说明

- 用户计划过于简略时：按合理旅行逻辑补全，并明确哪些是**推断建议**而非客户承诺。
- **参考资料优先**：用户提供的材料及有针对性的检索结论优先于泛泛常识。
- **商用对齐**：用车、门票、餐食是否包含等，以合同 / 报价为准；勿擅自改写口径。

## 统一输入 Intake 规范（商用强制）

当用户提供行程资料时，先按以下优先级提取为文本，再进入后续流程：

1. 纯文本（聊天内容 / txt / md）：直接解析
2. 截图 / 图片：先 OCR 提取关键字段，再人工补齐不确定信息
3. 文档（docx/pdf，可能含图）：先抽取文本主干，再提取图片主题作为搜图关键词

最低必需字段（缺失则追问）：

- 日程骨架：`D1...Dn`
- 每日路线或核心节点
- 费用说明（至少要有包含项和成人价；儿童价可选）

推荐使用自动化脚本落地简版输入：

```bash
python scripts/roadbook_intake.py --input <brief.txt|brief.docx> --output-dir <目标目录>
```

## 工具可用性说明

本 skill 涉及的外部数据工具均为 **CLI 命令**，需通过 **Bash** 工具调用，而非 MCP 工具或 Skill：

| 工具 | 类型 | 调用方式 | 用途 |
|------|------|---------|------|
| `flyai` | npm CLI | `Bash: flyai search-flight ...` | 机票、酒店、景点门票实时数据（飞猪） |
| TikHub API | REST（`.env` 中 `TIKHUB_API_KEY`） | `python3 scripts/tikhub_xhs_search.py -k "关键词"` | 小红书笔记搜索、配图、正文 |
| `mcp__grok-search__web_search` | MCP 工具 | 直接调用 | 通用网络搜索（降级方案） |

**Agent 委派规则**：当使用 Agent 工具并行搜索时，subagent prompt 中**必须明确指示**通过 Bash 调用 `flyai` CLI 与 **`scripts/tikhub_xhs_search.py`**，而非仅依赖 `mcp__grok-search__web_search`。示例 prompt 片段：

```
Use Bash to run these CLI commands for real-time data:
- flyai search-flight --origin "成都" --destination "昆明" --dep-date 2026-04-02
- flyai search-hotels --dest-name "弥勒" --check-in-date 2026-04-03 --check-out-date 2026-04-05
- python3 scripts/tikhub_xhs_search.py -k "弥勒 带娃 亲子游" --limit 5 --pretty
Fall back to mcp__grok-search__web_search only when CLI tools fail or return no results.
```

## 第一步：收集旅行要素

在开始研究之前，先确认以下信息（已知的跳过，缺失的向用户询问）：

| 要素 | 说明 | 必需 |
|------|------|------|
| 出发地 | 从哪里出发（决定交通方案和可达性筛选） | 是 |
| 出行日期 | 具体日期或月份 | 是 |
| 天数 | 行程总天数 | 是 |
| 人员构成 | 谁去、有无老人小孩（含年龄） | 是 |
| 目的地 | 城市/地区，**可以为空**（进入推荐流程） | 否 |
| 预算范围 | 总预算或每日预算 | 否 |
| 偏好 | 饮食禁忌、兴趣方向、小众/热门、体力水平 | 否 |

## 第二步：目的地推荐（如目的地未知）

当用户不确定去哪里时，**先做目的地筛选，再做行程规划**。

**关键依赖**：目的地推荐分为多个阶段，Phase A 的结果是后续阶段的输入，**严禁跳过**：
- **Phase A**（可并行）：候选发现 + 季节检查 + 人群适配 → 输出候选短名单
- **Phase B**（依赖 Phase A）：仅对短名单中的候选地搜索交通 → 输出可行性排名
- **Phase B2**（依赖 Phase A，与 Phase B 可并行）：获取候选地出行日期的天气预报 → 降雨天数统计
- **Phase C**（依赖 Phase B + B2）：综合对比（含天气） → 呈现给用户决策

### Phase A：候选目的地发现与初筛

Phase A 内部的三个子步骤可通过 Agent **按区域并行**执行（如同时搜索"广西候选"和"云南候选"），每个 Agent 负责一个区域的完整初筛（发现 + 季节 + 人群适配）。

#### A1. 候选目的地发现

当用户给出的是**区域范围**（如"广西"、"云南"、"东南亚"）而非具体城市时，**必须先通过搜索获取候选目的地列表**，严禁仅凭一般知识预设。

**搜索策略**（每个区域 Agent 内部执行以下搜索）：

1. **FlyAI 极速搜索 — 从真实旅行产品反推目的地**：
```bash
flyai fliggy-fast-search --query "[区域] [天数]天 自由行"
flyai fliggy-fast-search --query "[区域] [月份] [特殊人群]游 攻略"
flyai fliggy-fast-search --query "[出发地]出发 [区域] [天数]日游"
```
返回的产品列表天然反映了哪些目的地有成熟旅游基础设施和可预订产品。从产品标题中提取目的地城市/地区。

2. **小红书获取真实用户验证的目的地**（优先 TikHub）：
```bash
python3 scripts/tikhub_xhs_search.py -k "[区域] [月份] 旅游 推荐" --limit 8 --fetch-detail
python3 scripts/tikhub_xhs_search.py -k "[出发地]出发 [区域] [特殊人群]游" --limit 8
```
如 TikHub 不可用，降级用 mcp__grok-search__web_search 搜索 `site:xiaohongshu.com [区域] [月份] 旅游 推荐`。

3. **mcp__grok-search__web_search 获取攻略型推荐**：
```
搜索示例：
- "[区域] [月份] 旅游推荐 目的地 [年份]"
- "[区域] [特殊人群] 旅游 去哪里好"
- "[出发地]出发 [区域] [天数]天 推荐"
```

#### A2. 季节性陷阱检查（与 A1 同一 Agent 内执行）

每个候选目的地必须检查：**核心吸引物在出行时段是否处于最佳状态**。

常见陷阱：
- 瀑布/水景 → 检查是否枯水期（如德天瀑布4月枯水，7-11月最佳）
- 花海/红叶 → 检查花期/叶期是否匹配
- 海滩 → 检查是否台风季/禁渔期
- 雪景/滑雪 → 检查是否已化雪

#### A3. 特殊人群适配评估（与 A1 同一 Agent 内执行）

如有婴幼儿（0-3岁），必须评估：
- **推车友好度**：景区路面是否平整可推车，是否有台阶/石板路/湿滑路段
- **母婴设施**：当地超市是否有奶粉/尿不湿，酒店是否提供婴儿床
- **医疗距离**：距最近有儿科的医院多远
- **安全风险**：是否有深水区/陡崖/湿滑台阶等
- **节奏适配**：是否适合慢节奏、是否有午休条件

如有老人，额外评估：海拔高反风险、无障碍设施、医疗可及性。

**Phase A 输出**：每个区域 Agent 返回候选城市/地区列表（通常 3-5 个），附带季节评估和人群适配结论。汇总后形成**候选短名单**（跨区域去重，淘汰季节陷阱严重的）。

---

### Phase B：交通可行性筛选（依赖 Phase A 结果）

**仅对 Phase A 输出的候选短名单搜索交通**，不预设目的地。

交通耗时决定目的地是否可行。从出发地到候选目的地的单程时间直接决定有效游玩天数。

判断标准：
- 单程 ≤ 4h → 理想，不浪费游玩时间
- 单程 4-6h → 可接受，需占用半天
- 单程 6-8h → 勉强，带婴幼儿/老人需谨慎
- 单程 > 8h → 除非行程 ≥ 7天，否则不推荐

搜索方法：查12306高铁时刻、航班直飞情况、自驾距离。**用 Agent 并行搜索**短名单中多个候选地的交通。

---

### Phase B2：出行日期天气预报（依赖 Phase A 结果，与 Phase B 可并行）

**当出行日期在15天预报范围内时，必须在 Phase C 对比决策之前获取所有候选目的地的逐日天气预报。** 降雨情况直接影响户外游玩体验，尤其对带婴幼儿/老人的行程影响极大，是目的地选择的关键决策因素。

**触发条件**：出发日期距今 ≤ 15天 → 强制执行；> 15天 → 跳过，在第三步再查。

**执行方法**：对 Phase A 短名单中的所有候选目的地，**并行搜索**各地出行日期段的天气预报。

数据源优先级：
1. **中国天气网** (weather.com.cn) 15天预报 — `mcp__grok-search__web_search` 搜索 `[目的地] 15天天气预报 site:weather.com.cn`
2. **和风天气** (qweather.com) — 搜索获取页面URL后 `mcp__grok-search__web_fetch` 抓取
3. AccuWeather — 英文备选

**必须提取的信息**（出行日期内每日）：
- 天气状况（晴/多云/阴/雨）
- 最高/最低气温
- 是否有降雨

**输出格式**：各候选目的地的逐日天气对比表 + 降雨天数统计。

**淘汰规则**：
- 出行日期内 **≥ 50% 天数降雨** → 标记为"天气风险高"，在 Phase C 中降权
- 带婴幼儿/老人时，连续降雨 ≥ 3天 → 建议优先排除

---

### Phase C：多目的地对比决策

将候选目的地按以下维度做横向对比表格呈现给用户：

| 维度 | 权重 |
|------|------|
| 交通可达性 | 最高 |
| 出行日期天气（降雨） | **最高** |
| 特殊人群适配 | 高 |
| 季节时令匹配 | 高 |
| 小众/人流量 | 中 |
| 景点丰富度 | 中 |
| 费用水平 | 低 |

**天气对比必须包含**：各目的地出行日期内的降雨天数、逐日天气摘要、气温范围。如 Phase B2 已获取预报数据，直接引用；否则标注"超出预报范围，仅参考气候均值"。

给出明确的排名和推荐理由，让用户做最终决策。

## 第三步：多维信息采集

目的地确定后进入深度采集。使用 **Agent 并行搜索**提高效率。

### 维度 1：精确天气预报（前置，影响穿衣和行程安排）

**如 Phase B2 已获取天气预报，直接复用数据，补充穿衣建议即可，无需重复搜索。**

若 Phase B2 未执行（目的地已确定或出行日期当时超出15天范围），则必须从权威天气数据源获取逐日预报，不能仅靠搜索引擎的笼统描述。

数据源优先级：
1. **中国天气网** (weather.com.cn) 15天预报 — 通过 mcp__grok-search__web_search 搜索 `[目的地] 15天天气预报 site:weather.com.cn`
2. **和风天气** (qweather.com) 30天预报 — 搜索获取页面URL后 mcp__grok-search__web_fetch 抓取
3. **wttr.in API** — 直接 `curl "https://wttr.in/[城市]?format=j1"` 获取3天JSON预报（适合近期出行）
4. AccuWeather — 英文备选

必须输出的天气信息（逐日）：
- 日期、天气状况（晴/多云/阴/雨）
- 最高/最低气温
- 降水概率或是否下雨
- 穿衣建议

### 维度 2：交通方案 + 衔接验证（关键！）

搜索大交通方案后，**必须验证关键衔接点的可行性**。

**机票搜索优先使用 FlyAI**（飞猪实时数据，含价格+航班号+可预订链接）：
```bash
flyai search-flight --origin "[出发地]" --destination "[目的地]" --dep-date YYYY-MM-DD --sort-type 3
# 往返加 --back-date，直飞加 --journey-type 1，限价加 --max-price
```
FlyAI 返回的 `adultPrice`、航班号、时刻为实时数据，可直接用于行程编排和预算。

**高铁/市内交通仍用 mcp__grok-search__web_search**：
```
搜索示例：
- "[出发地]到[目的地] 高铁 时刻表 [年份]"
- "[目的地] 市内交通 地铁/公交/打车/包车"
```

**衔接验证清单**（涉及转机/转高铁的行程必做）：

对每个"A交通→B交通"的转换节点，必须搜索并确认：
1. **A的到达时间** 和 **B的末班时间** — 确保来得及
2. **A→B的转场方式和耗时** — 如"机场→火车站"需确认距离、打车/地铁/大巴耗时
3. **缓冲时间是否充足** — 飞机落地后取行李+转场，至少预留1.5小时缓冲

```
衔接验证搜索示例：
- "[机场]到[火车站] 怎么去 多久 打车 地铁"
- "[火车站]到[目的地] 末班车 最晚几点"
- "[机场] 空港快线 [火车站] 时刻表 末班"
```

如果衔接不可行（如末班车赶不上），必须调整方案：
- 方案A：改更早的航班
- 方案B：到达城市住一晚，次日再转
- 方案C：机场直接包车/租车到目的地（跳过火车）

**在行程中明确标注衔接风险**，如"末班高铁约21:00，建议选17:00前落地的航班"。

### 维度 3：景点与活动（含门票价格）

**景点门票优先使用 FlyAI**（含门票价格、收费状态、可预订链接）：
```bash
flyai search-poi --city-name "[目的地]" --category "历史古迹"
# 可选：--keyword "景点名"、--poi-level 5（5A景区）
```
FlyAI 返回 `ticketInfo.price` 和 `freePoiStatus`（免费/收费），可直接用于预算。

**攻略和小众推荐仍用 mcp__grok-search__web_search 补充**：
```
搜索示例：
- "[目的地] 必去景点 推荐 攻略 [年份]"
- "[目的地] 亲子/家庭 玩法 推荐"（如有老人小孩）
- "[目的地] 小众景点 本地人推荐"
```

#### 景点/美食/玩法图片采集优先级（强制）

所有景点、美食、玩法等非酒店图片（含 `activities[].imageUrl`、`activities[].imageUrls`、`meal.imageUrl`）必须按下列顺序采集，**前序可用即停**：

| 优先级 | 来源 | 调用方式 | 说明 |
|---|---|---|---|
| 1️⃣ 首选 | **小红书（TikHub）** | `search_notes` → `get_note_info` 累计取图；交付默认每槽 **4** 张备选 URL（可用 `--min-images`/`--max-images` 或 `ROADBOOK_V2_IMAGE_ALTERNATES*` 配置） | 真实用户实拍，最贴近现场；具体流程详见下文"维度 6" |
| 2️⃣ 其次 | **通用图库** | `generate.py --auto-images --image-registry ...` 内置图库检索，或平台可用的通用图库 API | 仅在小红书无结果或不可用时使用 |
| 3️⃣ 再降级 | **兜底图源** | `generate.py` 内置 fallback | 保证版式不空，但相关性低于前两级 |
| 4️⃣ 最后 | **人工素材** | `image_registry.sources.manual` | 仅当前三层失败时启用 |

严禁把人工非酒店素材提前到兜底之前，除非用户明确要求。

### 维度 4：美食与餐饮

```
搜索示例：
- "[目的地] 必吃美食 餐厅推荐 人均"
- "[目的地] 当地人推荐 小吃 美食攻略"
```

### 维度 5：住宿方案（数据源强约束）

酒店**文字与结构化信息**（酒店列表、介绍、房型、设施、评分；报价类字段单独见下）须**严格按顺序获取**，**前序有可用结果即优先采用**，仅在前序无法满足时再进入下一步：

1. **携程**（第一顺位：核对酒店全称、介绍、房型与设施）
2. **飞猪**（第二顺位：`flyai search-hotels` 及返回字段，用于报价、链接近实时对齐）
3. **通用搜索**（第三顺位：`mcp__grok-search__web_search` 等；**仅作补充**，须在文案或数据旁标注「参考/待核验」，不得绕过前两步直接当主来源）

小红书、未标注来源的泛网页**不得**作为酒店**主信息**来源（实拍图可作为**图片**补充，见下文图片来源表）。

**第一顺位：携程**（优先官方页或明确 `site:ctrip.com` 结果）：
```bash
mcp__grok-search__web_search query="site:ctrip.com [酒店全称] 房型 设施"
mcp__grok-search__web_search query="site:ctrip.com [目的地] 酒店 [入住日期]"
```

**第二顺位：飞猪（FlyAI）**（指定入住离店日的列表与报价，用于与携程信息交叉核对）：
```bash
flyai search-hotels --dest-name "[目的地]" --check-in-date YYYY-MM-DD --check-out-date YYYY-MM-DD
# 可选：--poi-name "景点名"（按周边筛选）、--hotel-stars "4,5"、--max-price 800、--sort price_asc
```
FlyAI 返回 `price`（指定日期报价）、`score`、`review`、`detailUrl`（预订链接）等；**使用时仍应先已有携程侧酒店身份与房型描述**，再用飞猪对齐日期与价格。

**第三顺位：通用搜索**（携程、飞猪均无该酒店有效条目时再启用）：
```bash
mcp__grok-search__web_search query="[酒店全称] 官方 房型 含早"
```

酒店信息采集顺序（信息与报价汇总）：**携程 → 飞猪 → 搜索 → 人工**。若四步均无效，须明确标注「酒店数据缺失」，不得用小道消息凑数。

**预算中住宿价格处理**：在已按 **携程 → 飞猪** 核对前提下，FlyAI 传入具体日期的报价可标注为「实查」；仅凭第三步通用搜索或未传日期的检索结果，标注为「参考价」。

**roadbook-v2：住宿「简介」交付标准化（与维度 5 顺序一致）**：

- **原则**：**携程**侧先核对酒店全称、房型与设施（人工或 `site:ctrip.com`）；**飞猪**侧用固定脚本拉取长简介并写入 `feature` / `subtype: 住宿` / `items[].description`，保证每次**字数下限**与**命令参数**一致，避免口头约定漂移。
- **前置**：每条住宿卡片的 `description` 须保留 **`备选酒店：……`** 或 **`【拟定酒店】……`** 清单（以便解析首选店名）；`items[].title` 为飞猪检索用地名（如 **「西江」** 对应脚本内 **雷山** 别名，无需手改）。
- **强制命令（交付时勿省略日期与 min-chars）**：
  ```bash
  cd "[skills-travel-planner 仓库根]"
  python3 scripts/enrich_hotel_intro_from_flyai.py "[路书目录]/tripData.json" \
    --check-in YYYY-MM-DD \
    --check-out YYYY-MM-DD \
    --min-chars 200
  ```
  - `--check-in` / `--check-out`：**必须与该路书客户首晚入住/离店日一致**（与报价核对用同一窗口）；勿依赖 `meta.generationDate` 隐式默认作为交付凭据。
  - `--min-chars`：交付默认 **200**（与校验脚本思路一致：可再调高，但勿低于 200）。
  - 依赖：本机已安装 `flyai`（`npm i -g @fly-ai/flyai-cli`）。脚本第二顺位对齐飞猪列表字段，**不能替代**携程核对；成稿可在浏览器编辑或手工合并携程表述。
  - 已生成长简介且仍带清单的条目**默认跳过**；需按新日期重拉时加 **`--force`**。
- **与生成顺序**：一键 **`deliver_roadbook_v2.py`** 会先跑 **`enrich_daily_descriptions_from_xhs`**，再执行本脚本，随后 **小红书预检 + 按槽搜图** → **`validate`** → **`generate.py`**（禁令与命令模板见 **`AGENTS.md`**）。若分拆手工执行，建议在 **`fill_xhs_images`** 之前先写完住宿简介与每日正文。

### 维度 6：小红书/社区真实反馈（关键增量信息）

小红书上的真实用户帖子能提供搜索引擎找不到的细节（如推车是否真的好推、具体哪家店踩雷）。

**通过 TikHub API 访问小红书**（仓库根 `.env` 配置 `TIKHUB_API_KEY`，在 https://user.tikhub.io 申请）：

```bash
cd "[skill_assets_dir]/.."
# 写入 .env：export TIKHUB_API_KEY="你的密钥"
python3 scripts/tikhub_xhs_search.py -k "目的地 攻略" --limit 5 --fetch-detail --pretty
```

说明：
- **配图 / 每日正文 / deliver** 流水线已统一走 TikHub（`fill_xhs_images.py`、`enrich_daily_descriptions_from_xhs.py`），**不再使用 mcporter / 本地 xiaohongshu-mcp**。
- 节流：环境变量 **`ROADBOOK_FILL_XHS_COOLDOWN_MS`**（每次 TikHub 请求后停顿）、**`ROADBOOK_FILL_XHS_SLOT_GAP_MS`**（每槽之间）、**`ROADBOOK_FILL_XHS_MAX_DETAIL_FEEDS`**；或 `fill_xhs_images.py` 的 **`--xhs-cooldown-ms` / `--xhs-slot-gap-ms` / `--xhs-max-detail-feeds`**。**降重复**：默认单笔记最多 **5** 张、每关键词轮询最多 **2** 张；**可选 pHash 去重**见 **`--visual-dedupe`**。
- **并发取图（默认开启）**：`--xhs-concurrency` / `ROADBOOK_FILL_XHS_CONCURRENCY`（默认 **4** 线程）。`tikhub_xhs_client` 用模块级 keep-alive `httpx.Client`，`xhs-note-cache` 已加 `threading.Lock`。第一轮 worker 间用初始全书指纹快照做 `slot_exclude`；主线程按 idx 顺序合并并做**前向去重**；不足槽**串行补一轮**，使用最新累积指纹集。`--visual-dedupe` 与并发互斥（SQLite phash 缓存非多线程安全），打开时自动回退 `concurrency=1`。
- **roadbook-v2 每槽备选图数量（交付默认 **4** 张 URL，一人一键、全员同参数；可调）**：
  - **首选**：在仓库根 **不经用户确认** 执行 **`scripts/deliver_roadbook_v2.py`**（含 **`--check-in` / `--check-out`**，**默认建议 `--hotel-force`**），一次性完成 **每日小红书正文**、**亮点/费用/服务 LLM 润色**（`enrich_roadbook_copy_from_llm`）、住宿简介、小红书补图、校验 + `generate.py`（参数与顺序见 **`scripts/deliver_roadbook_v2.py`** 或 **`AGENTS.md`**）。**禁止**问用户要不要跑交付。
  - **分拆执行时**（仅在一键脚本不可用时）：
  ```bash
  python3 scripts/fill_xhs_images.py "[路书目录]/tripData.json" \
    --min-images 4 --max-images 4 --require-remote-urls
  python3 scripts/validate_roadbook_image_alternates.py "[路书目录]/tripData.json" --require-remote-urls
  ```
  第二行须退出码 `0`。默认每槽 **4** 张备选；团队可用环境变量 **`ROADBOOK_V2_IMAGE_ALTERNATES`**（或 `_MIN` / `_MAX`）或 deliver **`--min-images` / `--max-images`** 统一改口径。**交付路径禁止** `fill_xhs_images.py --skip-existing`（仅本地提速可用）。草稿可去掉两行中的 `--require-remote-urls` 或改用 `deliver --allow-local-placeholders`。仅 `relink_local_roadbook_images.py` 不会补足多张远端备选。详情见 **`AGENTS.md`**。

**配图检索词（已内置）**：`fill_xhs_images.py` 调用 **`scripts/xhs_search_keyword_rules.py`**——封面/大标题背景偏向 **目的地 + 风景或建筑 + 大气**，并带 **分桶**（航拍/实拍/夜景等）；`daily` 槽位以 **当日 theme** 为主干轮换后缀，并带 **实拍/游客/航拍/栈道** 等桶词；住宿配图偏向 **酒店名 + 实拍/房型大堂/体验**。**交通**（`subtype: 交通` 的用车图 + 章节背景）**固定仅走飞猪 `flyai keyword-search`（网络 `picUrl`）**，不走小红书；见 **`image_fallback_chain.flyai_transport_*`** / **`refill_transport_images_from_flyai.py`**。

**每日 `description` 与住宿**：交付前 `daily.data.description` 建议 **120–280 字**；**`deliver`** 默认先跑 **`scripts/enrich_daily_descriptions_from_xhs.py --force`**（每次润色；自动识别 `[话题]`/emoji/机位清单等劣质拼接并重写；配置 **`OPENAI_API_KEY`** 或 **`DEEPSEEK_API_KEY`**（可写在仓库根 **`.env`**，由 `scripts/repo_dotenv.py` 在 `deliver` / `enrich_daily` 启动时加载）时经 OpenAI 兼容 Chat Completions **统一顾问文风**，否则以 **`overview` + 飞猪/维基洁净句** 合成；DeepSeek 专用变量与示例见 **`docs/deepseek-llm-setup.md`**；亦可用 **`OPENAI_BASE_URL` + `OPENAI_MODEL`** 指向 DeepSeek；**`--daily-no-llm`** 禁用 LLM；**`--no-daily-force`** 保留已有长文案；**`--skip-daily-enrich`** 跳过）。住宿长简介用 **`enrich_hotel_intro_from_flyai.py --min-chars 200`**（含舒适度、房型位置核对）。每日 Markdown 底稿规范见本文 **「行程详解文档 · Agent 角色提示词」** 章节。

1. **搜索帖子** — TikHub CLI 或交付流水线自动执行：
```bash
python3 scripts/tikhub_xhs_search.py -k "[目的地] 亲子游 带娃" --limit 10 --fetch-detail -o sources/xhs-亲子.json --pretty
python3 scripts/tikhub_xhs_search.py -k "[具体景点名] 实拍 打卡" --limit 5 --fetch-detail
```
对每个行程中的主要景点（3-5个核心景点），建议单独搜索一次以获取针对性的实拍图片。

2. **获取详情** — `--fetch-detail` 或对单条 `note_id` 再调 `get_note_info`（流水线内自动完成）。

3. **提取关键信息**：真实行程、避雷指南、酒店餐厅推荐、带娃tips

4. **提取笔记图片** — 从 JSON 的 `image_urls` 字段或 `fill_xhs_images` 写入 tripData：
   - 优先选取**点赞/收藏量最高**的 3-5 篇笔记中的图片
   - 每个图片槽累计保留约定数量的高质量备选 URL（默认 **4**，封面图优先，不足时继续补源）
   - 图片 URL 通常在返回数据的 `imageList` 或 `images` 字段中
   - 将图片与对应景点/活动匹配：根据笔记搜索关键词（如"XX景点"）对应到 tripData 中相应的 activity
   - 直接写入 `activities[].imageUrl`（单张首选）或多张时取第一张作为 imageUrl
   - 若同一景点从多篇笔记获取了多张图片，选择来自收藏量最高笔记的那张

如 TikHub 不可用（密钥/配额），`fill_xhs_images` 可加 `--skip-xhs` 走飞猪→维基→占位兜底；策划阶段可用 `mcp__grok-search__web_search` 搜索 `site:xiaohongshu.com`。

## 第四步：行程编排

### 路线串联原则

1. **地理聚类**：相近景点同一天，减少无效通勤
2. **节奏交替**：暴走日后安排休闲日
3. **就近用餐**：餐厅选在当日景点附近
4. **弹性时间**：每天留 1-2 小时缓冲
5. **到达日轻松**：第一天只安排入住和周边，不赶景点

### 特殊人群适配

婴幼儿（0-3岁）：
- 每日最多 2 个景点，上午1个+下午1个
- 保留午睡时间（13:00-15:00 段不安排活动或安排车程）
- 标注每个景点是否需要推车/背带
- 餐厅选有儿童座椅或空间宽敞的

老人：
- 每日步行量 < 1.5万步
- 避免海拔 > 3000m 的景点（除非提前适应）
- 安排午休时间

### 每日行程结构

```
上午（9:00-12:00）：主要景点 + 交通方式
午餐（12:00-13:30）：推荐餐厅 + 特色菜 + 人均
下午（14:00-17:30）：次要景点或休闲活动（婴幼儿场景可安排午睡+轻活动）
晚餐（18:00-19:30）：推荐餐厅
晚上（19:30+）：夜间活动（夜市/散步/温泉/休息）
```

## 第五步：预算编制（含可靠度标注）

价格数据的精确程度取决于数据来源。**每项费用必须标注可靠度等级**：

### 可靠度分级

| 等级 | 含义 | 标注方式 | 示例 |
|------|------|---------|------|
| **实查** | 从权威平台（12306/官网）或 FlyAI（飞猪实时数据）获取的具体价格 | 无标注 | 高铁票¥34、FlyAI机票¥400 |
| **参考** | 来自搜索结果但非实时报价（起步价/区间/往年价） | 价格后加"~" | 酒店~¥750/晚 |
| **估算** | 无直接数据源，基于同类经验推断 | 价格后加"≈" | 市内打车≈¥500 |

### 预算表结构

| 分类 | 细项 | 注意 |
|------|------|------|
| 交通 | 大交通 + 市内 | 婴儿机票(2岁以下约成人10%)、婴儿高铁免票 |
| 住宿 | 房价 × 间数 × 晚数 | 4+大人通常需2间房，**标注为参考价** |
| 餐饮 | 人均 × 人数 × 餐数 | 婴幼儿不单独计餐费 |
| 门票 | 各景点门票 | 1.2m以下/6岁以下通常免票 |
| 其他 | 保险、伴手礼、杂项 | |

### 动态价格声明

在预算区域底部和 tips 中必须包含以下提醒：

> 机票和酒店为动态定价，以上为参考估算。出行前请在携程/12306确认实际价格并预订。

### 机票价格处理

**优先通过 FlyAI `search-flight` 获取实时报价**，返回的 `adultPrice` 为飞猪当前售价，可标注为"实查"。
如 FlyAI 不可用或未返回结果，则搜索 `[出发地]到[目的地] 机票 [月份] 价格` 获取大致区间，标注为"参考价"。
在 tips 中建议用户出行前在飞猪/携程确认最终价格并预订。

最终给出：**预估总费用** 和 **人均费用**，并注明"含参考价成分，实际以预订为准"。

## 第六步：生成 HTML

每次旅行计划应生成**独特的视觉外观**，而非千篇一律的固定模板。不同目的地、天气、季节、节日应有不同的色调、风格和氛围。

### 生成方法（两步）

#### Step 1：准备 tripData JSON

将所有行程数据组装为 tripData JSON 对象（结构见下方），额外添加 `"generationDate": "YYYY-MM-DD"` 字段。用 Write 工具写入目标文件夹（如 `[目的地]-[出发年月]/tripData.json`）。

#### Step 2：调用 ui-ux-pro-max 生成定制 HTML（首选）

根据目的地特征确定设计方向，然后调用 `ui-ux-pro-max` skill 生成单文件 HTML 页面。

**关键原则：动态 UI ≠ 换皮。** 不同目的地的页面必须在**布局结构**上有明显差异，而非仅更换配色/字体。以下是差异化维度：

| 维度 | 要求变化 | 示例 |
|------|---------|------|
| **页面布局** | 必须不同 | Bento Grid / 杂志分栏 / 横向滚动 / 卡片瀑布流 |
| **行程展示** | 必须不同 | 纵向时间轴 / 横向日卡选择器 / 手风琴折叠 / 卡片轮播 |
| **预算展示** | 必须不同 | 甜甜圈图+图例 / 堆叠条形图+表格 / 环形进度条 |
| **酒店展示** | 建议不同 | 横向滚动 / 网格 / 左图右文交替 |
| **色调/字体** | 必须不同 | 根据目的地自然特征选配色和字体风格 |
| **装饰元素** | 建议不同 | 目的地特色图形 motif（如铁轨虚线、海浪、山水轮廓） |

**设计方向提示词模板**（根据实际目的地调整）：

```
生成一个单文件旅行计划 HTML 页面。

设计方向：
- 目的地：[目的地名称及特征，如"弥勒温泉+建水古城，滇南田园风"]
- 色调：[根据目的地选择，如海滨→蓝白、古城→暖褐色、热带→翠绿橙、山水→水墨青]
- 季节：[当前季节和天气特征，如"4月春季，多云为主，薰衣草盛开"]
- 氛围：[目标感受，如"慢节奏温泉度假+人文古城探访"]
- 装饰元素：[可选，如紫陶纹理、红砖拱门、小火车插图等]
- 布局风格：[必须指定，如 Bento Grid / 杂志分栏 / 横向卡片 等]

功能需求（必须包含以下区域，但布局/交互方式自由发挥）：
- 概览区：标题、日期、人员、总预算、天气摘要
- 天气：逐日天气展示（图标+温度）
- 每日行程：含景点/交通/餐饮，支持展开/折叠
- 住宿推荐：卡片式，含价格、评分、亮点标签
- 预算明细：分类汇总 + 可查看细项
- 实用贴士
- 响应式（375px/768px/1024px）+ 打印友好

数据：以下 tripData JSON 对象通过 <script> 标签内联，JS 读取并渲染。
[粘贴 tripData JSON]
```

**注意**：ui-ux-pro-max 生成的 HTML 必须是**单文件**（CSS/JS 全部内联），确保离线可用。

### HTML 生成技术注意事项

以下是实际踩过的坑，生成 HTML 时必须遵守：

1. **禁止深层嵌套模板字符串**：JS 模板字符串 `` ` `` 内嵌 `.map()` 再嵌 `` ` `` 再嵌 `${}` 会导致解析错误。复杂的 innerHTML 拼接应使用 `+` 字符串连接或独立函数返回 HTML 片段。
2. **SVG 图标必须有尺寸约束**：裸 SVG 插入 HTML 时如果外部没有 `width/height` 限制的容器，会撑满父元素。必须用 `<span style="width:Npx;height:Npx;display:inline-flex">` 包裹，或在 SVG 标签上加 `width` `height` 属性。
3. **中文和特殊字符用 Unicode 转义**：在字符串拼接中，`·` 用 `\u00b7`，`°` 用 `\u00b0`，`¥` 用 `\u00a5`，`→` 用 `\u2192`，避免编码问题。
4. **生成后必须验证**：用 `node -e` 提取 `<script>` 内容并 `new Function()` 检查语法，确认无错后再打开浏览器。

#### Fallback：使用固定模板

如 ui-ux-pro-max 不可用或用户要求快速生成，降级使用 `assets/generate.py` + `assets/templates/default/template.html`（或 `--template` 别名）：

```bash
python "[skill_assets_dir]/generate.py" tripData.json "[目的地]-[天数]天旅行计划.html"
# 如需同时生成 PDF
python "[skill_assets_dir]/generate.py" tripData.json "[目的地]-[天数]天旅行计划.html" --pdf
# roadbook-v2：模板含 `@page A4` 与 `@media print` 防断裂；`generate.py` 默认写入 `meta.printLayout` 启发式分页（`--no-print-layout` 关闭）并对过长文案 WARN（`--no-content-length-warn` 关闭）。无头 `--pdf` 使用 Chromium `--run-all-compositor-stages-before-draw`。
# 自动补齐图片：所有平台必须带 --auto-images 和 --image-registry
python "[skill_assets_dir]/generate.py" tripData.json "[目的地]-[天数]天旅行计划.html" --auto-images --image-registry "[skill_assets_dir]/image_registry.sample.json" --min-images 3 --ensure-day-images --min-images-per-activity 2 --save-updated-json
# 严格准确模式（禁用随机图兜底，只保留可追溯图片来源，宁缺毋滥）
python "[skill_assets_dir]/generate.py" tripData.json "[目的地]-[天数]天旅行计划.html" --auto-images --image-registry "[skill_assets_dir]/image_registry.sample.json" --strict-images --save-updated-json
# ID驱动图片模式（推荐）：通过 hotelId/poiId + 图片注册表精准匹配
python "[skill_assets_dir]/generate.py" tripData.json "[目的地]-[天数]天旅行计划.html" --auto-images --image-registry "[skill_assets_dir]/image_registry.sample.json" --localize-images --save-updated-json
```

### tripData 数据结构

餐饮以 `meal` 字段嵌入 activities 数组（而非独立 meals 对象），与模板 JS 渲染逻辑一致：

```javascript
const tripData = {
  title: "目的地 N日游",
  dateRange: "2026-04-05 ~ 04-10",
  travelers: "2大1小",
  weather: {
    summary: "晴为主，偶有阵雨",
    avgHigh: 25, avgLow: 16,
    rainfall: "30%",
    clothing: "短袖+薄外套",
    tips: "注意防晒"
  },
  days: [
    {
      date: "04/05", weekday: "周六", theme: "初见京都",
      weather: { icon: "sunny", high: 24, low: 15 },
      activities: [
        { time: "09:00", name: "伏见稻荷大社", duration: "2h", cost: 0, transport: "JR奈良线", note: "千鸟居打卡", imageUrl: "https://..." },
        { time: "12:00", name: "午餐", meal: { name: "餐厅名", cuisine: "日料", perPerson: 80, recommended: "推荐菜", location: "步行5分钟" } }
      ]
    }
  ],
  hotels: [
    { name: "酒店名", area: "区域", pricePerNight: 600, highlights: "近地铁;含早餐", rating: "4.5", imageUrl: "https://..." }
  ],
  budget: {
    transport: { items: [{ name: "机票×2", cost: 2000 }], subtotal: 3000 },
    accommodation: { items: [...], subtotal: 2400 },
    food: { items: [...], subtotal: 1800 },
    tickets: { items: [...], subtotal: 500 },
    other: { items: [...], subtotal: 300 },
    total: 8000, perPerson: 4000
  },
  tips: ["实用贴士1", "实用贴士2"]
};
```

weather.icon 可选值：`sunny`, `cloudy`, `overcast`, `rainy`, `stormy`, `snowy`, `partlyCloudy`

图片字段（可选）：
- `activities[].imageUrl`：景点/活动图片 URL（建议 16:9）
- `hotels[].imageUrl`：酒店封面图 URL（建议 16:9）
- `activities[].poiId`：景点唯一ID（用于图片注册表精准匹配）
- `hotels[].hotelId`：酒店唯一ID（用于图片注册表精准匹配）

图片来源优先级（所有平台强制）：
1. 非酒店图片：小红书 → 通用图库 → 兜底 → 人工。
2. 酒店图片：携程 → 飞猪 → 小红书 → 人工 → 通用图库 → 兜底。
3. 酒店信息（文案/房型/设施等）：**携程 → 飞猪 → 搜索**（通用检索，须降级标注）→ 人工。

生成路书或旅行计划 HTML/PDF 时，固定带上 `--auto-images --image-registry "[skill_assets_dir]/image_registry.sample.json"`。如用户提供自己的 registry，则替换 registry 路径但保留参数。

酒店信息来源规则：
- 酒店结构化信息与介绍性正文：**先携程、再飞猪（FlyAI）、再通用搜索**；不得跳序把搜索或小红书当主来源。
- 酒店图片可在上述信息落地后，按「酒店图片」优先级补充小红书实拍等。

小红书图片使用注意：
- 仅提取公开笔记中的图片 URL，用于个人旅行计划参考
- 图片 URL 可能有时效性，若后续失效会被 generate.py 的图片增强机制自动替换
- 每个景点/酒店至少尝试匹配 1 张小红书实拍图

### 输出目录结构

每次生成旅行计划时，在工作目录下创建独立文件夹，将本次所有相关数据集中存放：

```
[工作目录]/
└── [目的地]-[出发年月]/                  ← 如 "弥勒建水-2026-04"
    ├── tripData.json                     ← 行程数据（数据层，可复用修改后重新生成 HTML）
    ├── [目的地]-[天数]天旅行计划.html      ← 最终产出页面
    └── sources/                          ← 搜索原始数据存档（可选，方便溯源）
        ├── flights.json                  ← FlyAI 机票搜索结果
        ├── hotels.json                   ← FlyAI 酒店搜索结果
        └── xiaohongshu.json              ← 小红书笔记搜索+详情
```

**文件夹命名规则**：`[主要目的地]-[出发年-月]`，如 `弥勒建水-2026-04`、`桂林阳朔-2026-10`。

**生成流程**：
1. `mkdir` 创建文件夹
2. 将 tripData JSON 写入 `文件夹/tripData.json`
3. 将 FlyAI/小红书等搜索原始数据写入 `文件夹/sources/`（可选，便于后续溯源或更新数据）
4. 生成 HTML 写入 `文件夹/[目的地]-[天数]天旅行计划.html`
5. 用 `start`(Windows) 或 `open`(Mac) 打开浏览器预览

**好处**：
- 多次旅行计划互不干扰
- 修改 tripData.json 后可用 `assets/generate.py` 快速重新生成
- sources 目录保留搜索快照，便于对比或离线查阅

**Cursor Agent（roadbook-v2 · 默认交付）**：生成或更新 v2 路书时须遵循 **`skill.md`**（本文）与 **`AGENTS.md`** 中的交付约定，在本机 **不经用户确认** 执行 **`deliver_roadbook_v2.py`**（默认 **strict 小红书 https 图**，勿加 `--allow-local-placeholders`），传入 `tripData.json`、`输出.html`、**`--check-in` / `--check-out`**，且 **默认附带 `--hotel-force`**。**禁止**询问「要不要跑交付/补图」。串联：**每日小红书正文** → **亮点/费用/服务 LLM**（`enrich_roadbook_copy_from_llm`）→ 住宿简介 → **预检 + fill_xhs `--require-remote-urls`** → **validate `--require-remote-urls`** → `generate.py`。用户明示草稿时才可加 **`--allow-local-placeholders`**（跳过每日正文 enrich 与 strict 配图）并标注非交付稿。TikHub 异常时检查 `TIKHUB_API_KEY` 与账户余额后重试。裂图可先 `relink_local_roadbook_images.py`，对客户交付仍需 strict deliver。

## 第七步：部署到 Cloudflare Pages

项目已通过 Git 集成 Cloudflare Pages，推送即自动部署。

### 部署流程

1. **新建旅行计划文件夹后**，将新文件加入 Git 并推送：
```bash
git add [目的地]-[出发年月]/
git add index.html
git commit -m "feat: add [目的地] travel plan"
git push
```

2. Cloudflare Pages 自动触发部署，无需手动操作。

### 首页索引维护

每次新增旅行计划后，**必须同步更新 `index.html`**，添加新计划的卡片链接。

链接路径规则（Cloudflare Pages 自动去除 `.html` 后缀）：
- `崇左-2026-04/崇左-6天旅行计划.html` → href=`崇左-2026-04/崇左-6天旅行计划`
- 目录名和文件名中的中文直接使用，浏览器自动编码

### Cloudflare Pages 构建配置

| 配置项 | 值 |
|---|---|
| Build command | 留空 |
| Build output directory | `/` |
| Deploy command | `exit 0` |

### 交付时的诚实声明

生成 HTML 后，必须向用户说明数据可靠度：

```
可靠数据：天气预报（来自中国天气网/和风天气）、高铁票价（来自12306）、小红书攻略（真实帖子）
FlyAI实时数据：机票价格和航班（飞猪实时报价）、酒店价格（指定日期报价）、景点门票（飞猪数据）
参考数据：mcp__grok-search__web_search获取的酒店/机票价格（起步价/区间）
需用户确认：高铁衔接班次（12306查出行当日）
```

同时提醒用户出行前需自行确认的 2 件事：
1. 通过 FlyAI 返回的预订链接或在飞猪/携程完成机票和酒店预订（锁定价格）
2. 在12306确认高铁衔接班次（尤其是涉及机场→火车站转场的）

## 路书生成模式（定制旅游服务商）

本模式面向定制旅游服务商，将「客户给的简版资料」标准化为可视化编辑的长滚动路书 HTML。

### 触发条件

当满足以下任一条件时进入路书生成模式：
- 用户提供 XLS/Excel 行程规划表格
- 用户提供纯文字日程（如 `D1...D6...`）和费用说明
- 用户提供截图/图片形式的行程表
- 用户提供文档（txt/md/docx/pdf）并要求出客户版路书
- 用户明确说"生成路书"、"做路书"、"出路书"
- 用户描述需求涉及"给客户看的行程"、"旅行社宣传用"

**roadbook-v2 默认交付执行（Agent · 勿询问用户）**：进入本模式并产出 **roadbook-v2** 时，在 `tripData.json` 就绪后 **必须自动执行** **`deliver_roadbook_v2.py`**（默认 strict，**不含** `--allow-local-placeholders`），含 **`--check-in` / `--check-out`**、**默认建议 `--hotel-force`**；流水线含 **每日小红书正文 enrich**、**亮点/费用/服务 LLM**、住宿飞猪简介、strict 配图与校验。**禁止**询问「要不要完整交付」「要不要跑小红书」；仅用户**明文**草稿/离线时可 **`--allow-local-placeholders`**（跳过每日正文与 strict 配图）。TikHub 异常：检查 **`.env`** 中 **`TIKHUB_API_KEY`** 与账户余额后重跑。

### 统一解析流程（文本 / 截图 / 文档）

1. **输入识别**
   - 纯文本：直接解析
   - 截图/图片：先 OCR 提取，再人工核对关键字段
   - 文档：先抽文字主干，图片仅用于“搜图关键词提示”
2. **字段抽取（最低可交付）**
   - `D1..Dn` 每日行程
   - 每日线路节点（`A-B-C`）
   - 费用信息（至少成人价 + 费用包含）
3. **结构化输出**
   - 统一落到 `roadbook-v2` 的 `tripData.json`
   - **默认紧接着执行交付流水线**（非草稿）：**strict** `deliver_roadbook_v2.py`（可选 **`--intake-brief`** 合并费用/服务简表 → **每日小红书正文** + 住宿 + **小红书 https 配图**〔**费用/服务正文页不配图**〕+ 校验 + HTML），**默认 MCP 单次超时 240s**，**无需用户二次确认**；详见上文与子 skill。

### roadbook-v2：`text-block` 费用说明与服务说明（模板一致 · 按输入筛项）

**费用说明**（`subtype: "费用"`）与 **服务说明**（`subtype: "服务"`）在 v2 中须 **同一套版式与数据形态**，便于客户阅读与浏览器内编辑一致：

| 项目 | 规则 |
| --- | --- |
| 版式 | **仅** `data.title`（大标题）+ **`data.content`（单块 HTML）**；同一渲染分支（`template-roadbook-v2.html` 中与 `规则`/`须知` 不同） |
| HTML 结构 | **推荐固定骨架**：一两段导语 `<p>…</p>` + 分项 **`<ul class="textblock-lines textblock-rich-bullets"><li>…</li></ul>`**；列表项对应客户资料里拆出的条目，可增删改 |
| 与「须知」区别 | **`须知` / `规则` / `其他`** 仍可用 `sections` 多分栏；**`费用` / `服务` 不要写 `sections`** |
| 按输入筛选 | **费用**：从资料中提取报价、包含/不含、用车/门票/保险等**计价与打包范围**相关句，写入费用块列表。**服务**：提取**服务范围、预订/签约、出行须知、注意事项、退改政策、服务承诺**等与履约/服务相关句，写入服务块；**不要**把纯计价条款塞进服务块，也不要把须知条款塞进费用块 |
| Intake / 合并 | `scripts/roadbook_intake.py` 从零生成 tripData；已有 JSON 时可用 **`scripts/merge_intake_fee_service.py`** 或由 **`deliver_roadbook_v2.py --intake-brief`** 把简表里费用类 / 服务类段落写入 **`text-cost-001` / `text-service-001`**（规则见脚本与 `extract_sections_from_text`）。**费用块**来自 `用车`…`费用不含` 及报价行；**服务块**来自 `服务说明`…`退改说明` 等（`SERVICE_SECTION_HEADINGS_ORDER`）。**导出 HTML 前** `generate.py` 对 **`subtype: 须知`（出行须知）**：**无正文则从 tripData 移除、不占版**；**有正文则并入「服务说明」**同一富文本页（与费用/服务版式一致），不因空块生成默认须知文案。 |
| 旧数据 | `lines` / 旧 `sections` 仅作迁移合并进 `content`；保存后以 `content` 为准 |

**Agent 手写 tripData 时**：先写 `title` 与导语 `<p>`，再把从客户材料中**筛出的相关条**各写成一条 `<li>`（可多条 `<ul>`，但建议与 Intake 一致用单个 `textblock-rich-bullets`）。两类组件仅在 **子类型与标题文案** 上区分，**正文 DOM 约定一致**。

### 快速落地脚本（推荐）

当输入是简版行程文本或 docx 时，优先用脚本生成首版 `tripData`：

```bash
python scripts/roadbook_intake.py --input <brief.txt|brief.docx> --output-dir <目的地-月份>
```

需要直接产出可预览 HTML：

```bash
python scripts/roadbook_intake.py --input <brief.txt|brief.docx> --output-dir <目的地-月份> --render --html-name <路书名>.html
```

**交付默认（专业 Skill）**：Intake 自带 `--render` 仍可能只是占位图或未达约定张数的备选 URL。**对客户交付**须在产出目录上 **再自动执行一遍**（替换路径与日期）：

```bash
python3 scripts/deliver_roadbook_v2.py \
  "<目的地-月份>/tripData.json" \
  "<目的地-月份>/<路书名>.html" \
  --check-in YYYY-MM-DD \
  --check-out YYYY-MM-DD \
  --hotel-force
```

（默认 **strict 小红书 https**；仅草稿可加末尾 **`--allow-local-placeholders`**。）

### XLS 解析流程（适用于表格客户）

1. **读取 XLS 文件**：直接读取表格内容，或使用 `python -c "import openpyxl; ..."` 解析
2. **识别表头行**：查找包含"时间|天数|行程区间|行程内容|用餐|住宿"的行作为表头
3. **逐行提取每日行程**：
   - 景点列表：按"-"或"—"分隔符拆分行程内容列（如"从江-堂安梯田-日落-侗族大歌-肇兴侗寨"→5个景点/节点）
   - 用餐安排：识别"早餐/午餐/晚餐"或"酒店含早"
   - 住宿酒店：提取酒店名称
   - 航班/车次：如有则提取
4. **提取报价区域**（通常在行程表格下方）：
   - 成人报价、儿童报价、总价
   - "报价包含"明细列表（酒店、用车、门票、保险、餐食等）
   - "报价不包含"明细列表（额外门票、个人消费、行程变更等）
5. **构建 tripData JSON**：按路书 schema 结构组装数据

### 多源图片采集

解析完 XLS 后，需为每个景点、酒店、美食采集配图：

**非酒店图片**（固定顺序：小红书 → 通用图库 → 兜底 → 人工）：
```bash
# 为每个景点单独搜索高赞实拍图
python3 scripts/tikhub_xhs_search.py -k "[景点名] 实拍 打卡" --limit 8 --fetch-detail
# 获取详情提取图片 URL
python3 scripts/tikhub_xhs_search.py -k "[景点名]" --limit 1 --fetch-detail
```
每个图片槽取约定数量的备选图片（roadbook-v2 交付默认 **4** 张 URL）：v1 数据首张写入 `imageUrl`、其余写入 `imageUrls` 备选；v2 数据所有图片槽统一写成 URL 数组或 `{ "alternates": [...], "slotLabel": "地点/槽位名" }`，数组第一张为默认展示，其余必须作为模板内"备选图片"。`generate.py --auto-images` **默认**仅走小红书（`--image-fallback none`）；小红书不可用或需要自动补洞时，可加 `--image-fallback wikimedia` 再尝试维基，或继续用 image registry / 人工替换素材。

**roadbook-v2 大标题背景图（强制）**：
- 封面 `cover.backgroundImage` 以及每个组件的 `data.backgroundImage` 都必须支持替换图片与选择备选图片。
- 生成 `tripData.json` 时不要只写单张字符串；每个大标题背景候选图须满足交付约定张数（默认 **4** 张，可由 deliver / 环境变量配置），例如 `{ "alternates": ["…"], "slotLabel": "肇兴侗寨 · 堂安梯田" }`。
- 浏览器编辑时必须先点击"编辑"，在每个大标题背景右下角确认出现"替换背景"；正常生成时应出现与交付约定一致的备选数量（默认 **4** 张），点击后应打开"选择备选图片"面板。
- 用户替换或选择备选图后，点击"保存"导出的 JSON 要保留当前图在首位，剩余候选图继续保留为备选。

**美食图片**（小红书）：
```bash
python3 scripts/tikhub_xhs_search.py -k "[目的地] [菜名] 美食 推荐" --limit 6
```

**酒店/房型图片与信息**：
- **酒店信息（介绍/房型/设施等）顺序**：**携程 → 飞猪（FlyAI）→ 通用搜索**（见上文「维度 5」）。
- **酒店图片顺序**：携程 → 飞猪 → 小红书 → 人工 → 通用图库 → 兜底。

```bash
# 1) 携程（先）
mcp__grok-search__web_search query="site:ctrip.com [酒店名] 房型 图片"
# 2) 飞猪 / FlyAI（次）
flyai search-hotels --dest-name "[目的地]" --check-in-date YYYY-MM-DD --check-out-date YYYY-MM-DD
# 3) 仍不足时再通用搜索（须标注参考）
mcp__grok-search__web_search query="[酒店全称] 房型 设施 含早"
```

**roadbook-v2「住宿安排」feature**：`subtype: 住宿` 在模板中为**单列加宽卡片**（顶栏标题 + **简介** + 右侧主图）。**交付级**简介须：**携程核对**后，再运行 **`scripts/enrich_hotel_intro_from_flyai.py`**（见上文「roadbook-v2：住宿简介交付标准化」固定参数），将飞猪检索结果拼为**不低于 `--min-chars 200`** 的正文写入 `items[].description`；禁止依赖隐式默认日期。仍缺信息时再用通用搜索并标注参考。配图 `slotLabel` 建议含店名与「外观/客房」（`roadbook_intake.py` 可生成占位）。

**车辆图片**：
```bash
mcp__grok-search__web_search query="[车型名] 旅游巴士 包车 图片"
# 如"33座旅游巴士 金龙 图片"
```

**封面图片**：
```bash
python3 scripts/tikhub_xhs_search.py -k "[目的地] 风景 航拍" --limit 6
```

### 输出流程

1. **生成数据**：将解析+采集结果写入 `[目的地]-路书/tripData.json`（住宿卡片须保留 **`备选酒店：…`** 或 **`【拟定酒店】…`** 清单供飞猪脚本解析）。

2. **（roadbook-v2 · 默认即本步，Agent 勿向用户确认）** 在 **`skill_assets_dir` 所在仓库根**自动执行（示例路径请替换）：
```bash
python3 scripts/deliver_roadbook_v2.py \
  "[目的地]-路书/tripData.json" \
  "[目的地]-路书/[目的地]-路书.html" \
  --check-in YYYY-MM-DD \
  --check-out YYYY-MM-DD \
  --hotel-force
```
   npm：`npm run roadbook:deliver -- "[目的地]-路书/tripData.json" "[目的地]-路书/[目的地]-路书.html" --check-in YYYY-MM-DD --check-out YYYY-MM-DD --hotel-force`  
   内部顺序：**`enrich_daily_descriptions_from_xhs.py`**（默认 **`--min-chars 120`**；可选 **`OPENAI_API_KEY` / `DEEPSEEK_API_KEY` LLM 文风**，见 **`docs/deepseek-llm-setup.md`**；检索词见 **`xhs_search_keyword_rules.daily_enrich_description_keywords`**）→ **`enrich_roadbook_copy_from_llm.py`**（亮点/费用/服务，默认 **`--force`**）→ **`enrich_hotel_intro_from_flyai.py`**（`--min-chars 200`）→ **`sync_brand_logo.py`**（封面固定「玩点旅行」Logo）→ **`fill_xhs_images.py`**（预检 + **`--require-remote-urls`**，默认 **`--min-images 4 --max-images 4`**，**禁止** `--skip-existing`；封面 Logo / 费用/服务不搜图）→ **`validate_roadbook_image_alternates.py --require-remote-urls`**（`--min` 默认与 deliver 一致）→ **`generate.py`**（`--template roadbook-v2 --no-serve --no-open --no-localize-images`：配图保持 **https**；品牌 Logo 由 **`sync_brand_logo`** 落本地）。草稿可加 **`deliver --allow-local-placeholders`**（同步跳过每日正文 enrich 与 strict 配图）。依赖：`flyai`、稳定可用的小红书 MCP、**可选 LLM 密钥**（每日正文）。

3. **（可选 · 极速草稿）** 用户**明示**「草稿/预览/离线」时可 **`deliver --allow-local-placeholders`** 或单独 `generate.py`；**未明示则一律视为 strict 交付**，须完成第 2 步（小红书 https 图门禁）。

4. **打开预览**：`open "[目的地]-路书/[目的地]-路书.html"`

5. **用户编辑**：在浏览器中打开后，点击顶部「编辑模式」即可：
   - 直接点击文字修改；
   - 点击普通图片替换 URL；
   - 封面/章节大标题右下角「替换背景」「备选 N 张」切换候选图；
   - 「保存 JSON」导出后再交付时，建议再跑第 2 步 **`deliver_roadbook_v2.py`**（可加 `--hotel-force`）。

### 路书 tripData 数据结构

路书 **v1** 模式使用扩展的 tripData schema（详见 `assets/templates/roadbook/roadbook-schema.sample.json`），关键字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `title` | string | 路书标题 |
| `coverImage` | string | 封面大图 URL |
| `subtitle` | string | 副标题/卖点 |
| `duration` | string | 行程天数，如"2天1晚" |
| `travelers` | string | 人员构成 |
| `origin` | string | 出发地 |
| `highlights` | string[] | 产品亮点列表 |
| `itineraryOverview` | array | 行程总览（每天一条摘要） |
| `days` | array | 每日详细行程（含 activities/meals/hotel） |
| `vehicles` | array | 车辆信息（车型/座位/图片） |
| `hotels` | array | 酒店信息（房型/图片/设施） |
| `foods` | array | 美食推荐（图片/描述） |
| `costs` | object | 费用明细（报价/包含/不包含） |
| `tips` | string[] | 注意事项 |

### 携程数据源

携程为酒店**信息第一顺位**来源（介绍、房型、设施等）；**第二顺位**为飞猪（FlyAI）；**第三顺位**为通用网页搜索（须标注参考）。当前可通过搜索引擎检索携程页面：
```bash
mcp__grok-search__web_search query="site:ctrip.com [酒店名]"
```

后续将接入携程 MCP 服务（类似 xiaohongshu-mcp），支持：
- 酒店搜索与房型列表
- 房间实拍图片获取
- 酒店设施与评分信息
- 周边推荐
