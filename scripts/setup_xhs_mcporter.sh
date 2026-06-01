#!/usr/bin/env bash
# 已废弃：路书流水线已全面改用 TikHub API，不再依赖 mcporter / 本地 xiaohongshu-mcp。
echo "此脚本已废弃。请在仓库根 .env 配置 TIKHUB_API_KEY，并使用：" >&2
echo "  python3 scripts/tikhub_xhs_search.py -k \"关键词\"" >&2
echo "  python3 scripts/fill_xhs_images.py …" >&2
exit 1
