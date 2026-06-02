#!/bin/bash
# 构建脚本 — 执行抓取和站点构建
#
# 用法:
#   bash build.sh              # 抓取 + 构建（默认）
#   bash build.sh --build-only # 仅构建站点（相当于后台管理的一键build）
#   bash build.sh --help       # 显示帮助

set -euo pipefail

if [ "${1:-}" = "--help" ]; then
    sed -n '3,7p' "$0"
    exit 0
fi

if [ "${1:-}" = "--build-only" ]; then
    echo "=== Starting build only ==="
    python -m fetcher.site_builder
    echo "=== Build complete ==="
    exit 0
fi

echo "=== Starting fetch and build ==="

# 执行抓取（单次运行）
python -m fetcher.scheduler --once

# 构建静态站点
python -m fetcher.site_builder

echo "=== Build complete ==="
