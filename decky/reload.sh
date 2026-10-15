#!/usr/bin/env bash
# 安全重载 Decky Loader。
#
# 为什么不直接 `systemctl restart plugin_loader`：
#   Decky 的 unit 是 KillMode=process，重启时旧插件后端不会被一起杀死，
#   它们仍占着 127.0.0.1:1337（从旧 loader 继承的监听 socket），
#   新 loader 绑定失败 → 崩溃 → systemd 反复重启（表现为所有插件都加载不出来）。
# 这里先停服务，再用 fuser 精确清掉 1337 的持有者，最后启动。
set -euo pipefail

echo "停止 plugin_loader..."
sudo systemctl stop plugin_loader || true
sleep 1

if sudo ss -ltn 2>/dev/null | grep -q ':1337'; then
  echo "清理占用 1337 的孤儿进程..."
  sudo fuser -k -9 1337/tcp 2>/dev/null || true
  sleep 1
fi

echo "启动 plugin_loader..."
sudo systemctl start plugin_loader
sleep 8

echo "状态: $(systemctl is-active plugin_loader)  重启次数: $(systemctl show plugin_loader -p NRestarts --value)"
if sudo ss -ltn 2>/dev/null | grep -q ':1337'; then
  echo "1337 已绑定 ✓"
else
  echo "警告：1337 未绑定，Decky 可能未就绪" >&2
fi
