#!/bin/bash
# 部署脚本 — 将构建产物部署到远程服务器

set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:?REMOTE_HOST not set}"
REMOTE_USER="${REMOTE_USER:?REMOTE_USER not set}"
REMOTE_DIR="${REMOTE_DIR:-/var/www/news}"
SSH_KEY="${SSH_KEY:-}"

cleanup() {
    if [ -n "${KEY_FILE:-}" ] && [ -f "$KEY_FILE" ]; then
        rm -f "$KEY_FILE"
    fi
}
trap cleanup EXIT

echo "=== Deploying to ${REMOTE_HOST} ==="

SSH_OPTS=(-o StrictHostKeyChecking=accept-new)
if [ -n "$SSH_KEY" ]; then
    KEY_FILE=$(mktemp)
    chmod 600 "$KEY_FILE"
    echo "$SSH_KEY" > "$KEY_FILE"
    SSH_OPTS+=(-i "$KEY_FILE")
fi

# 创建远程目录
ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "mkdir -p ${REMOTE_DIR}"

# 同步 dist 目录（SSH 选项必须通过 -e 参数传递给 rsync）
rsync -avz --delete -e "ssh ${SSH_OPTS[*]}" \
    dist/ \
    "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/"

echo "=== Deployment complete ==="
