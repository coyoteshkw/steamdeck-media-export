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
| `package.json` | **必须安装**，其中 `"type": "module"` 是 Decky 判定「按 ESM 加载」的依据 |
| `main.py` | 后端：开关 / 状态 / 端口；spawn 主程序服务 |
| `src/index.tsx` | 面板 UI（开关、地址、二维码、端口、信息） |
| `dist/index.js` | 构建产物（已提交，安装时无需再 build） |
| `install.sh` | 安装到 `~/homebrew/plugins/SteamMediaExport` |
| `reload.sh` | 安全重载 Decky（避免 1337 端口崩溃循环） |

### ⚠️ 两个必须知道的事

1. **`package.json` 必须一起安装。** Decky 的 `plugin.py` 靠它决定加载方式：

   ```python
   self.load_type = LEGACY_EVAL_IIFE              # 默认：用 eval() 当普通脚本跑
   if (plugin_dir / "package.json").is_file():
       if package_json.get("type") == "module":
           self.load_type = ESMODULE_V1           # 用 import() 按 ESM 跑
   ```

   少了它，我们 ESM 产物末尾的 `export { ... }` 会被 `eval` 成语法错误
   （`SyntaxError: Unexpected token 'export'`），面板直接加载不出来。

2. **别直接 `systemctl restart plugin_loader`。** unit 是 `KillMode=process`，
   旧插件后端会变成孤儿、继续占 `127.0.0.1:1337`，新 loader 绑不上就被 systemd
   反复重启（所有插件都挂）。用 `./reload.sh`。

## 构建

```bash
cd decky
npm install --legacy-peer-deps
npm run build          # 产出 dist/index.js
```

## 安装 / 重载

```bash
./install.sh                   # 薄壳（B2）：复制插件本体到插件目录（需 sudo）
./install.sh --self-contained  # 自包含（A）：额外把 steam_exporter/ 复制进 vendor/
./reload.sh                    # 安全重启 Decky
```

也可以优先用 Decky 设置里的「重载插件」按钮。

## 打包分发

```bash
./package.sh                    # 薄壳（B2）：SteamMediaExport-v<版本>.zip
./package.sh --self-contained   # 自包含（A）：SteamMediaExport-v<版本>-selfcontained.zip
```

| 模式 | 内容 | 适合 |
|---|---|---|
| 薄壳（默认） | 只插件本体（~48KB） | 本机 / 仓库也在同一路径的机器 |
| 自包含 | 额外带 `vendor/steam_exporter/`（~500KB） | **分发给别人**，对方没有本仓库 |

两者都符合 Decky「从 ZIP 安装」的要求（恰好一层 `插件名/plugin.json`）。
安装：Decky 设置 → 开发者 → 从 ZIP 安装。

**主程序来源的判定顺序**（面板「代码来源」会显示实际用的哪一个）：

1. 设置里的 `project_dir`（自定义）
2. 包内 `vendor/`（内置，只有自包含包才有）
3. 默认仓库路径 `~/code-study/steam-recording-exporter`

所以：**自包含包分发出去后不依赖仓库**；本机开发用薄壳包，改主程序后
开关关开一次即可生效（无需重打包）。

> ⚠️ 两种模式都要求目标机器有 `python3` 和 `ffmpeg`（SteamOS 自带）。

## 可调设置

设置存在 `~/homebrew/settings/SteamMediaExport/settings.json`（由 Decky 的 `SettingsManager` 管理）：

| 键 | 默认 | 说明 |
|---|---|---|
| `port` | `8787` | 服务端口 |
| `project_dir` | `~/code-study/steam-recording-exporter` | 自定义主程序路径；留空则按「内置 → 仓库」顺序自动选 |
| `out_dir` | `~/Videos/SteamExports` | 默认输出目录 |
| `token` | `hello` | 访问令牌 |
