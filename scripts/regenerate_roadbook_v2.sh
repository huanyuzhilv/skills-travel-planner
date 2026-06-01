#!/usr/bin/env bash
# 从 tripData.json 重新生成 roadbook-v2 HTML。
# 默认：--template roadbook-v2 --no-serve --no-open（只写文件，不占 8888、不弹浏览器）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  echo "用法: $0 <tripData.json> <输出.html> [其它 generate.py 参数...]" >&2
  echo "" >&2
  echo "示例:" >&2
  echo "  $0 \"generated-roadbooks/贵州黔南环线-2026-05/tripData.json\" \"generated-roadbooks/贵州黔南环线-2026-05/贵州黔南6天路书.html\"" >&2
  echo "  $0 trip.json out.html --localize-images" >&2
  echo "  $0 trip.json out.html --auto-images --save-updated-json" >&2 >&2
  echo "" >&2
  echo "等价于在项目根执行:" >&2
  echo "  python3 assets/generate.py <tripData.json> <输出.html> \\" >&2
  echo "    --template roadbook-v2 --no-serve --no-open [其它参数...]" >&2
  echo "" >&2
  echo "需要生成后自动开本地预览服务时不要用本脚本，请直接:" >&2
  echo "  python3 assets/generate.py ... --template roadbook-v2" >&2
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
if [[ $# -lt 2 ]]; then
  usage
  exit 1
fi

TRIP="$1"
OUT="$2"
shift 2

exec python3 "$ROOT/assets/generate.py" "$TRIP" "$OUT" \
  --template roadbook-v2 \
  --no-serve \
  --no-open \
  "$@"
