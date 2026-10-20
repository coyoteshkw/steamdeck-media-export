#!/usr/bin/env bash
# 在桌面模式创建「Steam 媒体导出」快捷方式（双击 = 起服务 + 打开浏览器）。
#
# 用法：
#   ./bin/install-desktop-shortcut.sh            # 放到 ~/Desktop
#   ./bin/install-desktop-shortcut.sh ~/桌面     # 指定目录
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-$HOME/Desktop}"
mkdir -p "$TARGET"

DESKTOP_FILE="$TARGET/steam-media-export.desktop"
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Steam Media Export
Name[zh_CN]=Steam 媒体导出
Comment=Start the recording/screenshot web UI and open it in the browser
Comment[zh_CN]=启动录制/截图管理网页并打开浏览器
Exec=$DIR/bin/steamexport-serve
Path=$DIR
Icon=applications-multimedia
Terminal=true
Categories=Utility;AudioVideo;
EOF
chmod +x "$DESKTOP_FILE"

# 同时放进应用菜单
APPS="$HOME/.local/share/applications"
mkdir -p "$APPS"
cp "$DESKTOP_FILE" "$APPS/steam-media-export.desktop"

# KDE / GNOME：标记为可信，避免双击时提示「不受信任」
if command -v gio >/dev/null 2>&1; then
  gio set "$DESKTOP_FILE" metadata::trusted true 2>/dev/null || true
  gio set "$APPS/steam-media-export.desktop" metadata::trusted true 2>/dev/null || true
fi

echo "已创建桌面快捷方式：$DESKTOP_FILE"
echo "已加入应用菜单：$APPS/steam-media-export.desktop"
echo
echo "双击即可启动（默认 http://<本机IP>:8787/?token=hello）。"
echo "想改端口/口令：编辑 $DIR/bin/steamexport-serve 顶部的默认值，"
echo "或设置环境变量 STEAMEXPORT_PORT / STEAMEXPORT_TOKEN。"
