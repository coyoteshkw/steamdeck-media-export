#!/usr/bin/env bash
# 把本目录安装为 Decky 插件（默认 ~/homebrew/plugins/SteamMediaExport）。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="${DECKY_PLUGIN_DIR:-$HOME/homebrew/plugins/SteamMediaExport}"

if [ ! -d "$PLUGIN_DIR" ]; then
  echo "创建 $PLUGIN_DIR（需要 sudo 密码）"
  sudo mkdir -p "$PLUGIN_DIR"
  sudo chown "$(id -un):$(id -gn)" "$PLUGIN_DIR"
fi

if [ ! -f "$DIR/dist/index.js" ]; then
  echo "缺少 dist/index.js，请先构建： (cd '$DIR' && npm install --legacy-peer-deps && npm run build)" >&2
  exit 1
fi

cp "$DIR/plugin.json" "$DIR/main.py" "$PLUGIN_DIR/"
rm -rf "$PLUGIN_DIR/dist"
cp -r "$DIR/dist" "$PLUGIN_DIR/dist"

echo "已安装到 $PLUGIN_DIR"
echo "生效：运行 ./reload.sh，或使用 Decky 设置里的「重载插件」"
