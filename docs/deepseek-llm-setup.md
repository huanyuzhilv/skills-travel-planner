# DeepSeek：每日行程描述 LLM 配置

`scripts/enrich_daily_descriptions_from_xhs.py` 使用 **OpenAI 兼容** 的 `POST {base}/v1/chat/completions`。DeepSeek 官方 API 与此兼容，本仓库支持两种方式。

---

## 方式 A：专用环境变量（推荐）

只需 DeepSeek 时，在 shell 或 **仓库根目录 `.env`** 中设置（`deliver_roadbook_v2.py` 与 `enrich_daily_descriptions_from_xhs.py` 启动时会自动加载 `.env`，**不覆盖**已在环境里 export 的同名变量）：

```bash
export DEEPSEEK_API_KEY="sk-……"
```

或写入 **项目根** 的 `.env` 文件（与 `scripts/` 同级）：

```env
DEEPSEEK_API_KEY=sk-……
```

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | **必填**（与 `OPENAI_API_KEY` 二选一；若两者都设，**优先 `OPENAI_API_KEY`**）。 |
| `DEEPSEEK_BASE_URL` | 可选，默认 `https://api.deepseek.com/v1`。 |
| `DEEPSEEK_MODEL` | 可选，默认 `deepseek-chat`（推理版可用 `deepseek-reasoner`，通常更慢、更贵）。 |

然后照常跑交付或单独 enrich：

```bash
python3 scripts/enrich_daily_descriptions_from_xhs.py \
  "路书目录/tripData.json" --force

python3 scripts/deliver_roadbook_v2.py \
  "路书目录/tripData.json" "路书目录/路书名.html" \
  --check-in YYYY-MM-DD --check-out YYYY-MM-DD
```

---

## 方式 B：沿用 `OPENAI_*` 指向 DeepSeek

若你更习惯统一用「OpenAI 兼容」三个变量：

```bash
export OPENAI_API_KEY="sk-……"   # 填 DeepSeek 控制台里的 Key
export OPENAI_BASE_URL="https://api.deepseek.com/v1"
export OPENAI_MODEL="deepseek-chat"
```

此时**不要**再设 `DEEPSEEK_API_KEY`，否则会按优先级走 `OPENAI_API_KEY`。

---

## 命令行覆盖模型

```bash
python3 scripts/enrich_daily_descriptions_from_xhs.py \
  "路书目录/tripData.json" --force --llm-model deepseek-chat
```

`--llm-model` 优先于环境变量中的默认模型。

---

## 仅影响范围

- **仅**每日组件 `daily.data.description` 的 LLM 润色（及 `deliver` 串联的该步骤）。
- **不**改变配图、住宿飞猪简介、小红书 MCP；亦不经过 Cursor 对话里的模型。

---

## 故障排查

| 现象 | 建议 |
|------|------|
| 仍显示「未设置 … 顾问合成」 | 确认仓库根有 `.env` 且含 `DEEPSEEK_API_KEY=` 一行；或已在终端 `export`；路径须与 `scripts/` 同级（不是路书子目录）。 |
| HTTP 401 | Key 错误或过期。 |
| HTTP 400 / 模型不存在 | 检查 `DEEPSEEK_MODEL` / `--llm-model` 是否与控制台文档一致。 |

模型与计费以 [DeepSeek 开放平台](https://platform.deepseek.com/) 文档为准。
