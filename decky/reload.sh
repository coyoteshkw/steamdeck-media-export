#!/usr/bin/env bash
# 安全重载 Decky Loader。
#
# 为什么不能直接 `systemctl restart plugin_loader`：
#   Decky 的 unit 是 KillMode=process，重启时旧插件后端不会被一起杀死，
#   它们仍占着 127.0.0.1:1337（从旧 loader 继承的监听 socket），
#   新 loader 绑定失败 → 崩溃 → systemd 反复重启。
# 这里先停服务、清掉残留的 loader 与插件后端，再启动。
set -euo pipefail

HOME_DIR="${HOME:-/home/deck}"
PLUGIN_ROOT="$HOME_DIR/homebrew/plugins"
LOADER_BIN="$HOME_DIR/homebrew/services/PluginLoader"

echo "停止 plugin_loader..."
sudo systemctl stop plugin_loader || true
sleep 1

echo "清理残留进程..."
sudo pkill -9 -f "$LOADER_BIN" 2>/dev/null || true
sudo pkill -9 -f "$PLUGIN_ROOT/" 2>/dev/null || true
sleep 2

echo "启动 plugin_loader..."
sudo systemctl start plugin_loader
sleep 6

echo "状态: $(systemctl is-active plugin_loader)  重启次数: $(systemctl show plugin_loader -p NRestarts --value)"
if sudo ss -ltn 2>/dev/null | grep -q ':1337'; then
  echo "1337 已绑定 ✓"
else
  echo "警告：1337 未绑定，Decky 可能未就绪" >&2
fi
