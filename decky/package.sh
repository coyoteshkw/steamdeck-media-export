#!/usr/bin/env bash
# 打包 Decky 插件 ZIP。
#
# 两种模式：
#   默认              —— 薄壳（B2）：不含主程序，依赖目标机器上的项目仓库
#   --self-contained  —— 自包含（A）：把 steam_exporter/ 一起打进 vendor/，
#                        分发到没有项目仓库的机器也能直接跑
#
# Decky「从 ZIP 安装」要求压缩包内恰好有一个 <文件夹>/plugin.json（深度 1），
# 见 decky-loader browser.py；否则报 No/Multiple plugin.json found。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$(cd "$DIR/.." && pwd)"
cd "$DIR"

SELF_CONTAINED=0
case "${1:-}" in
  --self-contained | --vendor) SELF_CONTAINED=1 ;;
  "") ;;
  -h | --help)
    echo "用法: $0 [--self-contained]"
    exit 0
    ;;
  *)
    echo "未知参数: $1（用法: $0 [--self-contained]）" >&2
    exit 2
    ;;
esac

NAME="$(python3 -c 'import json;print(json.load(open("plugin.json"))["name"])')"
VERSION="$(python3 -c 'import json;print(json.load(open("package.json"))["version"])')"
SUFFIX=""
[ "$SELF_CONTAINED" = 1 ] && SUFFIX="-selfcontained"
OUT="$DIR/$NAME-v$VERSION$SUFFIX.zip"

echo "构建前端..."
npm run build >/dev/null

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
PKG="$STAGE/$NAME"
mkdir -p "$PKG"

cp plugin.json package.json main.py README.md "$PKG/"
cp -r dist "$PKG/dist"

if [ "$SELF_CONTAINED" = 1 ]; then
  echo "内置主程序代码：$PROJECT/steam_exporter"
  mkdir -p "$PKG/vendor"
  cp -r "$PROJECT/steam_exporter" "$PKG/vendor/steam_exporter"
  find "$PKG/vendor" -name "__pycache__" -type d -prune -exec rm -rf {} +
  find "$PKG/vendor" -name "*.pyc" -delete
fi

rm -f "$OUT"
(cd "$STAGE" && zip -qr "$OUT" "$NAME")

echo
echo "已生成：$OUT  （$(du -h "$OUT" | cut -f1)）"
unzip -l "$OUT" | sed 's/^/  /'
