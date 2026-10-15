#!/usr/bin/env bash
# 把本目录安装为 Decky 插件（默认 ~/homebrew/plugins/SteamMediaExport）。
#
#   ./install.sh                  薄壳（B2）：运行时用本地仓库里的主程序
#   ./install.sh --self-contained 自包含（A）：把 steam_exporter/ 复制进 vendor/，
#                                  不依赖仓库路径
#
# 注意：Decky 在加载插件时会把插件目录 chown 成 root，所以重新安装一般需要 sudo。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$(cd "$DIR/.." && pwd)"
PLUGIN_DIR="${DECKY_PLUGIN_DIR:-$HOME/homebrew/plugins/SteamMediaExport}"

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

if [ ! -f "$DIR/dist/index.js" ]; then
  echo "缺少 dist/index.js，请先构建： (cd '$DIR' && npm install --legacy-peer-deps && npm run build)" >&2
  exit 1
fi

if [ ! -d "$PLUGIN_DIR" ]; then
  echo "创建 $PLUGIN_DIR"
  sudo mkdir -p "$PLUGIN_DIR"
fi

# 关键：必须带上 package.json，且其中要有 "type": "module"。
# Decky 的 plugin.py 用它决定加载方式（module → import()，否则 eval()）。
# 缺了它，ESM 产物末尾的 `export { ... }` 会被 eval 成语法错误。
sudo cp "$DIR/plugin.json" "$DIR/package.json" "$DIR/main.py" "$PLUGIN_DIR/"
sudo rm -rf "$PLUGIN_DIR/dist"
sudo cp -r "$DIR/dist" "$PLUGIN_DIR/dist"

if [ "$SELF_CONTAINED" = 1 ]; then
  echo "内置主程序代码：$PROJECT/steam_exporter"
  sudo rm -rf "$PLUGIN_DIR/vendor"
  sudo mkdir -p "$PLUGIN_DIR/vendor"
  sudo cp -r "$PROJECT/steam_exporter" "$PLUGIN_DIR/vendor/steam_exporter"
  sudo find "$PLUGIN_DIR/vendor" -name "__pycache__" -type d -prune -exec rm -rf {} +
  sudo find "$PLUGIN_DIR/vendor" -name "*.pyc" -delete
else
  sudo rm -rf "$PLUGIN_DIR/vendor"
fi

echo "已安装到 $PLUGIN_DIR"
echo "生效：运行 ./reload.sh，或使用 Decky 设置里的「重载插件」"
