#!/usr/bin/env bash
# 打成可分发的 Decky 插件 ZIP。
#
# Decky「从 ZIP 安装」的要求（见 decky-loader browser.py）：
#   压缩包内必须恰好有一个 `<文件夹>/plugin.json`（深度 1），
#   否则会报 "No plugin.json found in plugin ZIP" / "Multiple plugin.json found"。
# 所以这里把文件装进一个顶层文件夹再压缩。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

NAME="$(python3 -c 'import json;print(json.load(open("plugin.json"))["name"])')"
VERSION="$(python3 -c 'import json;print(json.load(open("package.json"))["version"])')"
OUT="$DIR/$NAME-v$VERSION.zip"

echo "构建前端..."
npm run build >/dev/null

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
PKG="$STAGE/$NAME"
mkdir -p "$PKG"

# 只打包运行时需要的文件（不含 src/、node_modules/、脚本）
cp plugin.json package.json main.py README.md "$PKG/"
cp -r dist "$PKG/dist"

rm -f "$OUT"
( cd "$STAGE" && zip -qr "$OUT" "$NAME" )

echo
echo "已生成：$OUT  （$(du -h "$OUT" | cut -f1)）"
echo "内容："
unzip -l "$OUT" | sed 's/^/  /'
