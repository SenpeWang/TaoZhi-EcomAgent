#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$HOME/.local/share/ecom-agent/runtime/node/bin:$PATH"
cd "$ROOT/web"
mkdir -p "$ROOT/data/runtime"
STAGING="$(mktemp -d "$ROOT/web/.build-XXXXXX")"
trap 'rm -rf "$STAGING"' EXIT
npm run build -- --outDir "$STAGING" > "$ROOT/data/runtime/frontend-build.log" 2>&1
# 原页面可能仍请求旧资源，保留已经发布的哈希资源，避免切换时缺失。
if [ -d dist/assets ]; then
 cp -n dist/assets/* "$STAGING/assets/" 2>/dev/null || true
fi
mkdir -p dist/assets
cp "$STAGING"/assets/* dist/assets/
mv "$STAGING/index.html" dist/index.html
printf '%s\n' '前端构建成功，工作台入口已更新'
