# Steam 媒体导出 · Decky 插件

在 Steam Deck **游戏模式**里，一键开关本项目的网页管理页面，并把手机可访问的地址与**二维码**直接显示在侧栏面板上。

## 功能

- **开关**：启动 / 停止 `steamexport serve`（默认关闭，不记忆状态）
- **手机访问地址**：优先选 `192.168.x.x`（其次 `172.16-31.x.x`、`10.x.x.x`）
- **二维码**：内容为完整地址，已带 `?token=hello`
- **端口**：默认 `8787`，面板里可改；若被占用会**自动顺延**到下一个空闲端口
- **信息回显**：输出目录 / 下载缓存 / 回收站 / 预览缓存（与命令行启动输出一致）
- 启动前若输出目录不存在会**自动创建**

## 使用

1. 游戏模式按 `...` 打开快捷菜单 → 点 **Decky** 图标 → **「Steam 媒体导出」**
2. 打开 **「启用服务」** 开关（默认关闭、不记忆上次状态）
3. 面板会出现手机访问地址和**二维码**，手机扫一下即可打开管理页
4. 不用时把开关关掉即可停止服务

面板上还能：

- 看到**代码来源**（内置 / 仓库）以及输出目录、下载缓存、回收站、预览缓存等路径
- 改**端口**（默认 `8787`，被占用会自动顺延）→ 点「应用端口并重启」
- 服务未启动时地址会**灰显**、二维码**不显示**（避免扫了连不上）

## 架构

插件本身不做任何媒体处理，只负责 spawn 主程序：

```
Decky 面板 (React)  ──call()──▶  decky/main.py (Decky 自带 Python)
                                      │  subprocess
                                      ▼
                         /usr/bin/python3 -u -m steam_exporter serve
                                      cwd = <主程序目录>
```

`<主程序目录>` 三选一（判定顺序见下文「打包分发」）：设置里的 `project_dir` /
包内 `vendor/`（自包含包）/ 默认仓库路径。

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

## 改完代码要做什么

**B2（薄壳）下，主程序改动完全不用碰插件**：插件只是启动仓库里的 `serve`，
每次启动都现读仓库代码。只有改 `decky/` 里的东西才需要重装插件。

| 改了什么 | 要 build | 要装插件 | 要重打 ZIP | 怎么生效 |
|---|---|---|---|---|
| `steam_exporter/web/*`（HTML/JS/CSS） | 否 | 否 | 否 | **刷新浏览器**（静态文件每次请求读盘） |
| `steam_exporter/*.py`（后端） | 否 | 否 | 否 | **重启服务**（插件里关开开关，或重启 `serve`） |
| `decky/src/index.tsx`（面板 UI） | **是** | **是** | 分发时 | `npm run build` → `install.sh` → `reload.sh` |
| `decky/main.py` / `plugin.json` / `package.json` | 否 | **是** | 分发时 | `install.sh` → `reload.sh` |
| 自包含包（A）已经分发给别人 | 否 | 否 | **是** | 重新 `package.sh --self-contained` |

> 一句话：**改主程序不用跑任何脚本**（前端刷新、后端重启服务）；
> 只有改 Decky 面板本身才需要 `build + install.sh + reload.sh`。
> `package.sh` 只在「要分发」时才需要，本机调试不用。

### 两套版本号

- **插件版本**：`decky/package.json` 的 `version`（当前 `0.1.x`），决定 ZIP 文件名。
- **主程序版本**：`steam_exporter/__init__.py` / `pyproject.toml`（当前 `0.4.x`），
  显示在网页顶栏和插件面板里。
- 自包含 ZIP 名用的是**插件版本**，里面捆的 `vendor/steam_exporter` 是打包那一刻的
  **主程序版本**（内容以 ZIP 生成时间为准）。

## 打包分发

```bash
./package.sh                    # 薄壳（B2）：SteamMediaExport-v<版本>.zip
./package.sh --self-contained   # 自包含（A）：SteamMediaExport-v<版本>-selfcontained.zip
```

| 模式 | 内容 | 适合 |
|---|---|---|
| 薄壳（默认） | 只插件本体（~48KB） | 本机 / 仓库也在同一路径的机器 |
| 自包含 | 额外带 `vendor/steam_exporter/`（~130KB） | **分发给别人**，对方没有本仓库 |

两者都符合 Decky「从 ZIP 安装」的要求（恰好一层 `插件名/plugin.json`）。
安装：Decky 设置 → 开发者 → 从 ZIP 安装。

**主程序来源的判定顺序**（面板「代码来源」会显示实际用的哪一个）：

1. 设置里的 `project_dir`（自定义）
2. 包内 `vendor/`（内置，只有自包含包才有）
3. 默认仓库路径 `~/code-study/steam-recording-exporter`

所以：**自包含包分发出去后不依赖仓库**；本机开发用薄壳包，改主程序后
开关关开一次即可生效（无需重打包）。

> 自包含包里的 `vendor/` 是**打包那一刻的快照**。之后改了主程序还想分发新版本，
> 需要重新 `./package.sh --self-contained`，否则对方跑的还是旧代码。

> ⚠️ 两种模式都要求目标机器有 `python3` 和 `ffmpeg`（SteamOS 自带）。

## 发布到 GitHub Release

仓库自带 `.github/workflows/release-decky.yml`：推送 `v*` 或 `decky-v*` 标签后，
GitHub Actions 会自动构建并把**自包含 ZIP** 附加到对应的 Release：

```bash
git tag v0.4.14
git push origin v0.4.14
```

Release 里会出现：

- `SteamMediaExport-v0.1.5-selfcontained.zip`（自包含，直接可装）

别人在 Releases 页下载自包含 ZIP 后，用 Decky 设置 → 开发者 → 从 ZIP 安装即可。

> ZIP 名用 `decky/package.json` 的插件版本；标签建议用主程序版本。
> 只想发插件版本时也可以打 `decky-v0.1.5`。

不想用 Actions 的话，也可以本地 `./package.sh --self-contained` 后，
在 GitHub 网页上创建 Release 并手动上传 ZIP。

## 常见问题

### `GLIBCXX_x.x.x not found` / `libopenmpt` 报错

Decky 的插件后端跑在 PyInstaller 里，环境变量里会带
`LD_LIBRARY_PATH=/tmp/_MEI…`，里面的旧 `libstdc++.so.6` 会让系统 ffmpeg 报：

```
/usr/bin/ffmpeg: …/libstdc++.so.6: version `GLIBCXX_3.4.32' not found
                (required by /usr/lib/libopenmpt.so.0)
```

插件从 **0.1.6** 起会在 spawn `serve` 时清掉 `LD_LIBRARY_PATH` / `LD_PRELOAD` /
`PYTHONHOME`；主程序从 **0.4.15** 起 spawn ffmpeg 时也会清掉
`LD_LIBRARY_PATH` / `LD_PRELOAD`。遇到这个报错时：升级插件到 0.1.6+ 后**关开一次开关**，
或在终端里手动跑 `./bin/steamexport-serve`（终端环境本来就是干净的）。

## 可调设置

设置存在 `~/homebrew/settings/SteamMediaExport/settings.json`（由 Decky 的 `SettingsManager` 管理）：

| 键 | 默认 | 说明 |
|---|---|---|
| `port` | `8787` | 服务端口 |
| `project_dir` | 未设置（自动） | 自定义主程序路径；判定顺序：内置 `vendor/` → 默认仓库路径 |
| `out_dir` | `~/Videos/SteamExports` | 默认输出目录 |
| `token` | `hello` | 访问令牌 |
