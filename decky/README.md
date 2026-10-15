# Steam 媒体导出 · Decky 插件

在 Steam Deck **游戏模式**里，一键开关本项目的网页管理页面，并把手机可访问的地址与**二维码**直接显示在侧栏面板上。

## 功能

- **开关**：启动 / 停止 `steamexport serve`（默认关闭，不记忆状态）
- **手机访问地址**：优先选 `192.168.x.x`（其次 `172.16-31.x.x`、`10.x.x.x`）
- **二维码**：内容为完整地址，已带 `?token=hello`
- **端口**：默认 `8787`，面板里可改；若被占用会**自动顺延**到下一个空闲端口
- **信息回显**：输出目录 / 下载缓存 / 回收站 / 预览缓存（与命令行启动输出一致）
- 启动前若输出目录不存在会**自动创建**

## 架构（B2）

插件本身只是**薄壳**，不做任何媒体处理：

```
Decky 面板 (React)  ──call()──▶  decky/main.py (Decky 自带 Python)
                                      │  subprocess
                                      ▼
                         /usr/bin/python3 -m steam_exporter serve
                                      cwd = ~/code-study/steam-recording-exporter
```

- 项目代码**零改动**，改完主程序只要在面板里关开一次开关即可生效，**无需重新打包插件**。
- 只有改面板 UI（`src/index.tsx`）时才需要 `npm run build` + 重载插件。

## 目录

| 文件 | 说明 |
|---|---|
| `plugin.json` | Decky 插件元数据（`name` 必须与插件目录同名） |
| `main.py` | 后端：开关 / 状态 / 端口；spawn 主程序服务 |
| `src/index.tsx` | 面板 UI（开关、地址、二维码、端口、信息） |
| `dist/index.js` | 构建产物（已提交，安装时无需再 build） |
| `install.sh` | 安装到 `~/homebrew/plugins/SteamMediaExport` |
| `reload.sh` | 安全重载 Decky（避免 1337 端口崩溃循环） |

## 构建

```bash
cd decky
npm install --legacy-peer-deps
npm run build          # 产出 dist/index.js
```

## 安装 / 重载

```bash
./install.sh           # 首次会用 sudo 建目录并 chown 给 deck
./reload.sh            # 安全重启 Decky
```

> ⚠️ 不要直接 `systemctl restart plugin_loader`：Decky 的 unit 是 `KillMode=process`，
> 旧插件后端会成为孤儿进程继续占着 `127.0.0.1:1337`，导致新 loader 起不来而被 systemd
> 反复重启。`reload.sh` 会先停服务、清掉孤儿进程再启动。
> 优先用 Decky 设置里的「重载插件」按钮也可以。

## 可调设置

设置存在 `~/homebrew/settings/SteamMediaExport/settings.json`（由 Decky 的 `SettingsManager` 管理）：

| 键 | 默认 | 说明 |
|---|---|---|
| `port` | `8787` | 服务端口 |
| `project_dir` | `~/code-study/steam-recording-exporter` | 主程序路径 |
| `out_dir` | `~/Videos/SteamExports` | 默认输出目录 |
| `token` | `hello` | 访问令牌 |
