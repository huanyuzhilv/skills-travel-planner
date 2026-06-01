#!/usr/bin/env bash
# probe-flyai-hotel.sh
# 目的：一次性探测 flyai CLI 的酒店接口，收集足够证据用于
#       更新 skill.md 第 282-287 行的酒店字段描述。
#
# 用法：
#   chmod +x scripts/probe-flyai-hotel.sh
#   ./scripts/probe-flyai-hotel.sh                                  # 默认海拉尔
#   ./scripts/probe-flyai-hotel.sh "上海" 2026-08-15 2026-08-16      # 自定义
#
# 依赖：bash, flyai, jq   (macOS: brew install jq)
# 产出：.probe/flyai-hotel-<时间戳>/  目录下包含原始 JSON 与 report.md

set -u

DEST="${1:-海拉尔}"
CHECKIN="${2:-2026-08-15}"
CHECKOUT="${3:-2026-08-16}"

OUT="${PWD}/.probe/flyai-hotel-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"
REPORT="$OUT/report.md"

say()  { printf '\n### %s\n\n' "$*" | tee -a "$REPORT"; }
code() { printf '```\n%s\n```\n' "$*" | tee -a "$REPORT" >/dev/null; printf '%s\n' "$*"; }
kv()   { printf -- '- **%s**: %s\n' "$1" "$2" | tee -a "$REPORT"; }

{
  echo "# flyai 酒店接口探测报告"
  echo
  echo "- 目的地: $DEST"
  echo "- 入住: $CHECKIN"
  echo "- 退房: $CHECKOUT"
  echo "- 生成时间: $(date '+%F %T')"
  echo
} > "$REPORT"

# -------- 1. 环境检查 --------
say "1. 环境"
if ! command -v flyai >/dev/null 2>&1; then
  echo "❌ 未检测到 flyai CLI。安装：npm i -g @fly-ai/flyai-cli" | tee -a "$REPORT"
  exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "❌ 未检测到 jq。安装：brew install jq" | tee -a "$REPORT"
  exit 1
fi
kv "flyai"   "$(command -v flyai)"
kv "version" "$(flyai --version 2>&1 | head -n1 || true)"
kv "jq"      "$(jq --version)"

# -------- 2. help 全文 --------
say "2. flyai 子命令清单 (flyai --help)"
flyai --help >"$OUT/flyai-help.txt" 2>&1 || true
code "$(cat "$OUT/flyai-help.txt")"

say "3. flyai search-hotels --help"
flyai search-hotels --help >"$OUT/search-hotels-help.txt" 2>&1 || true
code "$(cat "$OUT/search-hotels-help.txt")"

# -------- 3. 调用 search-hotels --------
say "4. 实际调用 search-hotels"
if flyai search-hotels \
      --dest-name "$DEST" \
      --check-in-date "$CHECKIN" \
      --check-out-date "$CHECKOUT" \
      > "$OUT/search-hotels.json" 2>"$OUT/search-hotels.err"; then
  echo "✅ 调用成功，结果已保存到 search-hotels.json" | tee -a "$REPORT"
else
  echo "⚠️ 调用失败，stderr:" | tee -a "$REPORT"
  code "$(cat "$OUT/search-hotels.err")"
fi

say "5. search-hotels 返回结构"
jq 'if type=="array" then
      {type:"array", length:length, firstItemKeys:(.[0]|keys? // [])}
    elif type=="object" then
      {type:"object", topKeys:(keys), firstItemKeys:((.data//.hotels//.list//.result)?|.[0]?|keys? // [])}
    else {type:type} end' "$OUT/search-hotels.json" \
  > "$OUT/shape.json" 2>/dev/null || true
code "$(cat "$OUT/shape.json" 2>/dev/null || echo '(解析失败)')"

say "6. 搜索所有含 image/pic/cover/photo/room/bed/area/size 的字段路径"
jq -r '
  [paths(scalars) | map(tostring) | join(".")]
  | map(select(test("(?i)(image|pic|cover|photo|room|bed|area|size)")))
  | unique
' "$OUT/search-hotels.json" > "$OUT/hit-paths.txt" 2>/dev/null || true
code "$(cat "$OUT/hit-paths.txt" 2>/dev/null || echo '(无命中)')"

say "7. 提取所有看似图片 URL 的值（前 10 条）"
jq -r '
  [.. | strings | select(test("^https?://.*\\.(jpg|jpeg|png|webp)"; "i"))]
  | unique | .[0:10] | .[]
' "$OUT/search-hotels.json" > "$OUT/image-urls.txt" 2>/dev/null || true
code "$(cat "$OUT/image-urls.txt" 2>/dev/null || echo '(无命中)')"

# -------- 4. 探测详情/房型子命令 --------
say "8. 探测可能的酒店详情 / 房型子命令"
CANDIDATES=(hotel-detail get-hotel-detail hotel-info get-hotel-info search-rooms list-rooms hotel-rooms hotel-images get-hotel-images)
for sub in "${CANDIDATES[@]}"; do
  if flyai "$sub" --help >"$OUT/$sub-help.txt" 2>&1; then
    size=$(wc -c <"$OUT/$sub-help.txt" | tr -d ' ')
    if [ "$size" -gt 50 ]; then
      echo "- ✅ flyai $sub  （help 大小 ${size}B）" | tee -a "$REPORT"
    else
      echo "- ⚠️ flyai $sub  返回空或极短 help，大概率不存在" | tee -a "$REPORT"
      rm -f "$OUT/$sub-help.txt"
    fi
  else
    echo "- ❌ flyai $sub  不存在" | tee -a "$REPORT"
    rm -f "$OUT/$sub-help.txt"
  fi
done

# -------- 5. 若找到详情子命令，拿第一个酒店 ID 再跑一次 --------
say "9. 若有详情子命令，用第一条酒店 ID 再跑一次"
HID=$(jq -r '
  [.. | objects | select(.hotelId? or .shid? or .sid? or .id?)]
  | .[0] | (.hotelId // .shid // .sid // .id) // empty
' "$OUT/search-hotels.json" 2>/dev/null || true)
kv "第一条酒店 ID" "${HID:-(未能解析)}"

if [ -n "${HID:-}" ]; then
  for helpfile in "$OUT"/*-help.txt; do
    [ -e "$helpfile" ] || continue
    sub=$(basename "$helpfile" -help.txt)
    case "$sub" in flyai|search-hotels) continue;; esac
    # 猜测参数名
    arg=""
    for candidate in --hotel-id --hotelId --sid --shid --id; do
      if grep -q -- "$candidate" "$helpfile"; then arg="$candidate"; break; fi
    done
    [ -z "$arg" ] && continue
    echo "→ flyai $sub $arg $HID" | tee -a "$REPORT"
    flyai "$sub" "$arg" "$HID" > "$OUT/$sub.json" 2>"$OUT/$sub.err" || true
    jq -r '
      [paths(scalars) | map(tostring) | join(".")]
      | map(select(test("(?i)(image|pic|cover|photo|room|bed|area|size)")))
      | unique
    ' "$OUT/$sub.json" > "$OUT/$sub-hits.txt" 2>/dev/null || true
    echo "  命中字段:" | tee -a "$REPORT"
    code "$(cat "$OUT/$sub-hits.txt" 2>/dev/null || echo '(无)')"
  done
fi

# -------- 6. 结尾 --------
say "10. 下一步"
cat <<EOF | tee -a "$REPORT"
- 把 \`$OUT\` 整个目录（重点是 report.md + *.json + *-help.txt）打包发我。
- 我会据此更新 [skill.md](../../skill.md) 第 282-287 行的 flyai 酒店字段承诺，
  以及"酒店图片 / 房型 / 房间面积"的采集链路。
EOF

echo
echo "✅ 完成。打开报告：$REPORT"
echo "   完整目录：$OUT"
