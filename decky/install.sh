#!/usr/bin/env bash
# 把本目录安装为 Decky 插件（默认 ~/homebrew/plugins/SteamMediaExport）。
#
# 注意：Decky 在加载插件时会把插件目录 chown 成 root，所以重新安装一般需要 sudo。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="${DECKY_PLUGIN_DIR:-$HOME/homebrew/plugins/SteamMediaExport}"

if [ ! -f "$DIR/dist/index.js" ]; then
  echo "缺少 dist/index.js，请先构建： (cd '$DIR' && npm install --legacy-peer-deps && npm run build)" >&2
  exit 1
fi

if [ ! -d "$PLUGIN_DIR" ]; then
  echo "创建 $PLUGIN_DIR"
  sudo mkdir -p "$PLUGIN_DIR"
fi

# 关键：必须带上 package.json，且其中要有 "type": "module"。
# Decky 的 plugin.py 是用它来决定加载方式的：
#   package.json 里 "type" == "module"  → 按 ESM（import）加载
#   否则                                → 当作 legacy，用 eval() 跑
# 缺了它，ESM 产物末尾的 `export { ... }` 会被 eval 成语法错误
# （"SyntaxError: Unexpected token 'export'"），插件面板直接加载不出来。
sudo cp "$DIR/plugin.json" "$DIR/package.json" "$DIR/main.py" "$PLUGIN_DIR/"
sudo rm -rf "$PLUGIN_DIR/dist"
sudo cp -r "$DIR/dist" "$PLUGIN_DIR/dist"

echo "已安装到 $PLUGIN_DIR"
echo "生效：运行 ./reload.sh，或使用 Decky 设置里的「重载插件」"
