# steam-recording-exporter

从 **SteamOS / Steam** 导出**游戏录制**（剪辑 / 录像）和**游戏截图**的小工具，自带网页管理界面，
方便在手机、平板或另一台电脑上浏览、下载。也可以装成 **Decky Loader 插件**，在游戏模式的
快捷菜单里一键开关。

**主要特性**

- **无损导出**（`-c copy`，画质零损失、速度接近磁盘拷贝）
- **压缩导出**（libx264 / libx265 / SVT-AV1 / VAAPI 硬件编码，可调 CRF、缩放）
- 按游戏 / 类型 / 时间 / ID 精确挑选，支持并发导出与去重清单
- 自动合并 Steam 分段续写的后台录像（`xxx_0`、`xxx_1` …）
- **网页端**：列表、缩略图、5 秒预览、在线播放、下载到本机、删除进回收站
- **截图图库**：按游戏分类、筛选、多选逐张下载或打包成 ZIP
- 只依赖 **Python 标准库 + 系统 ffmpeg**，无需安装第三方包

> 目标平台是 Steam Deck / SteamOS；任何有 Steam + ffmpeg 的 Linux 都能用。

## 目录

- [快速开始](#快速开始)
- [语言](#语言)
- [网页管理界面](#网页管理界面)
- [Decky 插件](#decky-插件)
- [命令行参考](#命令行参考)
- [长录像的处理](#长录像的处理)
- [注意事项](#注意事项)
- [开发](#开发)
- [后续计划](#后续计划)
- [更新日志](#更新日志)
- [许可](#许可)

---

## 快速开始

### 环境要求

- Python ≥ 3.10
- `ffmpeg`（SteamOS 自带；其他系统 `sudo apt install ffmpeg`）

先检查环境：

```bash
./bin/steamexport doctor
```

会列出 ffmpeg、Steam 目录、各账号的录制数量，以及可用的编码器。

### 看看有哪些录制

```bash
./bin/steamexport users                 # 账号与录制数量
./bin/steamexport list                  # 列出（默认主账号）
./bin/steamexport list --type clip      # 只看剪辑
./bin/steamexport list --type background
./bin/steamexport list --game "Outer Wilds"
./bin/steamexport list --since 2026-09-01 --until 2026-09-30
./bin/steamexport list --json           # 机器可读
```

### 导出

```bash
# 无损，导出全部剪辑到 ~/Videos/SteamExports
./bin/steamexport export --type clip --all

# 无损，导出某款游戏的全部内容（剪辑 + 录像）
./bin/steamexport export --game "Outer Wilds" --all

# 导出到指定目录
./bin/steamexport export --all -o /run/media/deck/MyDisk/SteamExports

# 压缩导出（更小，适合发布）
./bin/steamexport export --game "Outer Wilds" --type clip --all \
    --mode compress --codec libx264 --crf 23 --preset veryfast

# 先预览计划，不写文件
./bin/steamexport export --all --dry-run

# 并发导出
./bin/steamexport export --type clip --all -j 4

# 分批导出（按时间升序前 50 条）
./bin/steamexport export --type clip --all --limit 50
```

> 为防止误操作，`export` **必须**指定范围（`--all` / `--game` / `--type` / `--since` / ID）。

---

## 语言

- **网页端**：顶栏的「EN / 中文」按钮切换；选择存在浏览器里，默认跟随浏览器语言。
- **终端**：`serve` 的启动信息为中英双语。
- **Decky 插件**：面板跟随系统 / Steam 语言自动切换。

> 注：日期选择器（`<input type="date">`）是浏览器原生控件，显示格式跟随
> 浏览器 / 系统语言，页面无法覆盖。

---

## 网页管理界面

无需额外依赖，只用 Python 标准库：

```bash
./bin/steamexport serve                                   # 启动并打开浏览器
./bin/steamexport serve --port 9000                       # 换端口
./bin/steamexport serve --no-open                         # 不自动打开
./bin/steamexport serve --host 0.0.0.0 --token mysecret   # 局域网 / 手机访问
```

顶栏可在 **录像 / 截图** 两个视图间切换；视图、截图模式、打开的游戏都会写进 URL，
刷新后停留在原处。

### 录制管理

- **按游戏分组的列表**：每行显示 Steam 图标，可展开查看剪辑 / 后台录像 / 前台录像；
  左侧复选框可整组全选（半选表示部分选中）
- 两栏布局：左侧导出设置与筛选，右侧列表与进度；窄屏自动堆叠
- 筛选（类型 / 游戏 / 日期 / ID 搜索）、排序（最近录制 / 体积 / 名称）
- 勾选后「导出选中」，或「导出全部匹配」
- 无损 / 压缩、编码器、CRF、并发、命名模板直接在页面调
- 「仅预演」先看计划和预计体积，不写文件
- **实时进度**（SSE）：整体进度 + 当前条目百分比 + 速度
- 导出可取消，会自动清理半成品
- **下载到本机**：每条录制右侧的下载按钮
  - 画质预设：原画无损（流式、瞬时）/ 快速 / 均衡 / 小体积(720p) / 硬件(VAAPI)
  - 无损走**流式**不落盘；压缩先编码到**临时缓存**，再以 MP4 + `Content-Length` +
    `Range` 提供下载（**可断点续传**）
  - 缓存按时长（默认 10 分钟）/ 容量（默认 5GB，LRU）自动清理，也可手动清理
- **预览与播放**：列表带缩略图，点开是 5 秒静音预览；播放按钮可在线看**前 5 分钟**
  （`-c copy` remux，不转码、不落盘）
- 「打开导出目录」在 Deck 上执行 `xdg-open`

### 截图图库

点顶栏「截图」进入（与录像共用同一个服务、Token 和局域网地址）：

- **默认平铺全部截图**（缩略图网格；每批 200 张，底部「加载更多」）
- 工具栏「**全部 / 按分类**」：按分类进游戏卡片（封面 + 张数），点开看该游戏的网格
- 缩略图直接用 Steam 已生成的 `thumbnails/`（约 13KB/张），懒加载
- 点缩略图看**大图**，可左右切换（支持 `←` / `→`）
- **筛选**：游戏关键词 / 标题、日期范围、仅看有标题的
- **下载**：
  - 单选 / 多选：逐张下载
  - 多选：「**打包选中**」把选中的截图打包成 ZIP
  - 侧栏「**打包下载全部结果**」打包当前筛选结果
  - ZIP 为**平铺**的 `时间.jpg`（原图、不压缩、流式、不落盘；重名自动加 ` (1)`）
- **全选**：在某个游戏的详情里多选时可用（「全部」视图已有侧栏打包，不重复提供）
- **删除**：默认移入回收站，可恢复（建议先关闭 Steam）

> 截图以**文件系统为准**（`760/remote/<gameid>/screenshots/`），`screenshots.vdf`
> 只用于补充精确时间、尺寸与标题；Steam 运行时 vdf 可能只写一半，工具会容错。

### 删除录制

- 每条右侧的垃圾桶按钮，或勾选后「删除选中」
- **默认移入回收站（可恢复）**；弹窗勾选「彻底删除」则不可恢复
- 侧栏「删除与回收站」可查看占用、**恢复全部**、**清空回收站**
- 检测到 Steam 正在运行时会提示，建议先关闭 Steam
- `serve --trash DIR` 可改回收站位置（默认 `~/.local/share/steam-exporter/trash`）

### 局域网访问（手机 / 平板 / 另一台电脑）

```bash
./bin/steamexport serve --host 0.0.0.0 --token 你的口令
```

启动后会打印可访问地址：

```
  局域网地址（手机 / 平板 / 另一台电脑都可访问）：
    http://192.168.0.103:8787/?token=你的口令
```

手机打开该地址即可，界面会自动变单栏。注意：

- **一定要加 `--token`**，否则同网段任何人都能操作（删除 / 导出）
- 只在可信的家庭局域网使用，**不要暴露到公网**
- CPU 和磁盘都在 Steam Deck 上，「打开导出目录」也是在 Deck 上执行

### HTTP API

后端接口也可供脚本或 Decky 插件直接调用：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/summary` | 账号、ffmpeg、编码器、默认设置 |
| GET | `/api/recordings` | 录制列表（支持筛选参数） |
| POST | `/api/export` | 创建导出任务（`dry_run` 时直接返回计划） |
| GET | `/api/jobs/{id}` | 任务状态快照 |
| GET | `/api/jobs/{id}/events` | SSE 进度事件流 |
| POST | `/api/jobs/{id}/cancel` | 取消任务 |
| GET | `/api/download` | 无损下载（流式，不落盘） |
| POST | `/api/download/prepare` | 准备压缩下载（命中缓存或起编码任务） |
| GET | `/api/download/file` | 下载缓存文件（支持 Range 续传） |
| GET | `/api/thumb/{id}` | 录制缩略图 |
| GET | `/api/preview/{id}` | 5 秒静音预览片 |
| GET | `/api/play/{id}` | 在线播放（最多前 5 分钟） |
| POST | `/api/cache/clear` | 清空下载缓存 |
| POST | `/api/delete` | 删除录制（默认进回收站） |
| GET | `/api/trash` | 回收站列表与占用 |
| POST | `/api/trash/restore` | 恢复全部 |
| POST | `/api/trash/purge` | 清空回收站 |
| GET | `/api/icon/{appid}` | 游戏图标（Steam 本地缓存） |
| GET | `/api/screenshots/summary` | 截图总览 |
| GET | `/api/screenshots/games` | 按游戏分组的截图统计 |
| GET | `/api/screenshots/list` | 截图列表（支持筛选） |
| GET | `/api/screenshots/zip` | 打包为 ZIP（支持筛选，或 `ids=` 指定截图） |
| GET | `/api/screenshot/thumb/{id}` | 截图缩略图 |
| GET | `/api/screenshot/full/{id}` | 截图原图（`?dl=1` 触发下载） |
| POST | `/api/screenshots/delete` | 删除截图（默认进回收站） |
| POST | `/api/reveal` | 在文件管理器中打开目录 |

---

## Decky 插件

装成 Decky 插件后，可以在 SteamOS **游戏模式**的侧栏里：

- 一个开关**启动 / 停止**网页管理服务（默认关闭）
- 显示手机可访问的地址（优先 `192.168.x.x`）与**二维码**（已含 `?token=hello`）
- 端口可改（默认 `8787`，冲突自动顺延）
- 回显输出目录 / 下载缓存 / 回收站 / 预览缓存，以及实际使用的**代码来源**

**用法**：游戏模式按 `...` → Decky 图标 → 「Steam 媒体导出」→ 打开「启用服务」→
手机扫码。完整说明见 [`decky/README.md`](decky/README.md)。

插件后端只负责以子进程方式运行本项目的 `serve`，**主程序代码零改动**。

### 改主程序不用重装插件

B2（薄壳）模式下，插件每次启动都跑仓库里的最新代码：

| 改了什么 | 要 build | 要装插件 | 生效方式 |
|---|---|---|---|
| `steam_exporter/web/*`（HTML/JS/CSS） | 否 | 否 | **刷新浏览器** |
| `steam_exporter/*.py`（后端） | 否 | 否 | **重启服务**（插件里关开开关） |
| `decky/src/index.tsx`（面板 UI） | 是 | 是 | `npm run build` → `install.sh` → `reload.sh` |
| `decky/main.py` / `plugin.json` 等 | 否 | 是 | `install.sh` → `reload.sh` |

`package.sh` 只在**要分发**时才需要。

### 安装 / 打包

```bash
cd decky
npm install --legacy-peer-deps && npm run build
./install.sh                   # 装薄壳版到 ~/homebrew/plugins/SteamMediaExport
./install.sh --self-contained  # 装自包含版（把主程序复制进 vendor/）
./reload.sh                    # 安全重载 Decky

./package.sh                   # 薄壳 ZIP
./package.sh --self-contained  # 自包含 ZIP（分发给没有本仓库的人）
```

| 方式 | 内容 | 适合 |
|---|---|---|
| 薄壳（默认） | 只插件本体（约 48KB） | 目标机器上也有本仓库 |
| 自包含 | 额外带 `vendor/steam_exporter/`（约 130KB） | **分发给别人** |

两种 ZIP 都可在 Decky 设置 → 开发者 → 从 ZIP 安装。目标机器需有 `python3` 和
`ffmpeg`（SteamOS 自带）。

---

## 命令行参考

### `export` 常用参数

| 参数 | 说明 |
|---|---|
| `--all` | 导出所有匹配项 |
| `--game 关键词` | 按游戏名过滤，可多次 |
| `--type clip\|background\|foreground\|recording\|all` | 类型过滤（`recording` = 后台+前台） |
| `--since / --until YYYY-MM-DD` | 录制日期范围（按本地时区） |
| `ids`（位置参数） | 按 ID 或 ID 片段过滤 |
| `--user auto\|all\|SteamID` | 账号，默认 `auto` = 主账号 |
| `-o, --out DIR` | 输出目录，默认 `~/Videos/SteamExports` |
| `--mode lossless\|compress` | 无损（默认）或压缩 |
| `--codec` | `libx264`（默认）/`libx265`/`libsvtav1`/`h264_vaapi`/`hevc_vaapi`/`av1_vaapi` |
| `--crf` | 压缩质量，越小越清晰，默认 23（VAAPI 用 `-qp`） |
| `--preset` | x264/x265 速度预设，默认 `medium` |
| `--scale 1280:-2` | 压缩时等比缩放 |
| `--audio-bitrate` | 压缩音频码率，默认 `160k` |
| `--template` | 输出命名模板（见下） |
| `--no-faststart` | 不写 faststart（默认写入） |
| `--tmp DIR` | 临时符号链接/合并文件的目录 |
| `--no-trim` | 不按元数据时长裁剪尾部 |
| `--force` | 忽略清单，强制重新导出 |
| `--dry-run` | 只显示计划 |
| `-j, --jobs N` | 并发导出数 |
| `--no-manifest` | 不写去重清单 |

### 命名模板

默认：`{game}/{date} {time} - {kind_cn}.mp4`
例：`Outer Wilds/2026-09-05 17-22-00 - 剪辑.mp4`

| 变量 | 含义 | 示例 |
|---|---|---|
| `{game}` | 游戏名 | `Outer Wilds` |
| `{appid}` | Steam AppID（非 Steam 为规范化的 appid） | `753640` |
| `{gameid}` | Steam 内部 game id | `753640` |
| `{date}` | 本地日期 | `2026-09-05` |
| `{time}` | 本地时间 | `17-22-00` |
| `{datetime}` | 本地日期时间 | `2026-09-05 17-22-00` |
| `{kind}` | 类型（英文） | `clip` / `background` / `foreground` |
| `{kind_cn}` | 类型（中文） | `剪辑` / `后台录像` / `录像` |
| `{id}` | 源 ID | `clip_753640_20260905_092200` |
| `{title}` | Steam 记录的名称（若有） | `Outer Wilds - 2026-09-05 17:21:57` |
| `{duration}` | 时长 | `45m00s` |
| `{user}` | 账号 SteamID | `331943233` |

变量值里的路径分隔符会被替换成 `_`；`/` 只在模板里用于建子目录。模板生成的路径不能越出输出目录。

```bash
--template "{game}/{date}/{time} - {kind_cn}.mp4"
--template "Steam录制/{date} {time} {game}.mp4"
```

### 去重清单

每次导出会在输出目录写 `.steam_exporter_manifest.json`，记录已导出的源。再次运行时，
已存在且清单命中的会被跳过；`--force` 强制重导，`--no-manifest` 禁用清单。

### 无损 vs 压缩

| | 无损（`lossless`） | 压缩（`compress`） |
|---|---|---|
| 原理 | ffmpeg `-c copy` 重新封装 | 重新编码 |
| 画质 | 与录制完全一致 | 由 `--crf` / `--preset` 决定 |
| 速度 | 极快（≈ 磁盘拷贝） | 取决于 CPU / GPU |
| 体积 | 与原始一致（约 6.6 Mbps） | 可小到 1/5 ~ 1/10 |
| 推荐 | 存档 | 发布、分享、省空间 |

```bash
# 软件编码
./bin/steamexport export --all --mode compress --codec libx264 --crf 20 --preset slow
./bin/steamexport export --all --mode compress --codec libx265 --crf 24 --preset medium
./bin/steamexport export --all --mode compress --codec libsvtav1 --crf 30 --preset 6

# VAAPI 硬件编码（Steam Deck 上更快、更省电）
./bin/steamexport export --all --mode compress --codec h264_vaapi --crf 24
```

> 硬件编码的 `--crf` 实际传给 `-qp`，质量范围与软件编码不完全等价。

### 输出目录

```
~/Videos/SteamExports/
├── .steam_exporter_manifest.json     # 去重清单
├── Outer Wilds/
│   ├── 2026-08-22 22-44-15 - 剪辑.mp4
│   └── 2026-10-04 21-50-16 - 剪辑.mp4
└── Castle Crashers/
    └── 2026-10-14 23-51-59 - 后台录像.mp4
```

### 进度显示

单条导出（默认 `-j 1`）实时显示百分比、已处理/总时长与速度：

```
[1/1] 进度  73%  0:33/0:45  1.2x
```

重定向到文件时按整数百分比换行输出。多并发（`-j > 1`）时不显示逐条进度，只在每条完成时输出一行。

---

## 长录像的处理

Steam 录像是 DASH 分片，一个 51 分钟的剪辑就有上千个分片，会撞上两个系统限制：

1. **单个命令行参数 128 KiB 上限**（`MAX_ARG_STRLEN`）：把所有分片路径拼成一个
   `concat:` 参数会报 `Argument list too long`。解决：改用**短名符号链接**（不复制数据）。
2. **文件描述符上限**：ffmpeg 的 `concat:` 协议会同时打开所有分片，很多终端下软上限
   只有 1024，会报 `Too many open files`。解决：启动时自动把软上限提升到硬上限；
   若硬上限也很低，则回退到把分片合并成一个临时文件。

`--tmp DIR` 可指定临时文件目录（默认输出目录，避免写到内存盘）。

---

## 注意事项

1. **导出时最好别正在录制**：录制中的文件会被写入，建议先停止游戏/录制或关闭 Steam。
2. **时间戳**：Steam 目录名用的是 **UTC**，工具已换算成本地时间。
3. **后台录像是滚动缓冲**：Steam 只保留最近一段（默认约 1 小时），更早的分片会被删，
   无法找回；列表里的时长对这类录像是估算值。
4. **文件修改时间不可信**：SteamOS 上 mtime 可能异常，工具一律使用目录名/元数据里的时间。
5. **空间**：无损导出等于复制一份，导出前请确认目标盘剩余空间；`--dry-run` 可先看体积。
6. **游戏名解析**：优先用已安装游戏的 `appmanifest_*.acf`、非 Steam 快捷方式
   `shortcuts.vdf`，以及 `appcache/appinfo.vdf`（能补上「装过又卸载」的 Steam 游戏）。
   仍找不到的会显示为「未知应用 <appid>」。
7. **多账号**：`--user` 默认只处理主账号（`loginusers.vdf` 的 `MostRecent`）；
   要处理全部账号用 `--user all`。
8. **损坏录制自动恢复**：个别录制的视频头（`init-stream0.m4s`）可能为 0 字节。
   工具会跳过空文件，并尝试借用其它**同分辨率**录制的头部做 best-effort 恢复；
   无法恢复时明确提示并跳过，不影响其余导出。

---

## 开发

```bash
python3 -m unittest discover -s tests -v          # 运行测试
python3 -m steam_exporter --help                  # 以模块方式运行
```

### 项目结构

```
steam_exporter/
├── cli.py         # 命令行入口、过滤与命令实现
├── server.py      # HTTP 服务 + 网页后端（API / SSE / 流式下载）
├── web/           # 网页前端（index.html / app.js / style.css，无构建步骤）
├── library.py     # 账号选择、扫描、过滤（CLI 与网页共用）
├── scanner.py     # 扫描 gamerecordings，解析目录名与 clip.pb
├── screenshots.py # 扫描 Steam 截图 + screenshots.vdf 元数据
├── steam.py       # Steam 目录发现、VDF / appinfo 解析、游戏名解析
├── exporter.py    # ffmpeg 命令构建、命名模板、清单去重、并发
├── model.py       # Source 数据模型
├── pb.py          # 极简 protobuf 解析
└── util.py        # 格式化、mpd 时长、ffmpeg 调用等辅助
decky/             # Decky Loader 插件（薄壳 / 自包含）
docs/format.md     # Steam 录制存储格式说明
docs/CHANGELOG.md  # 完整更新日志
tests/             # 单元测试
bin/steamexport    # 便捷启动脚本
```

详见 [`docs/format.md`](docs/format.md)。

---

## 后续计划

- 导出时可选同时导出缩略图与时间线事件（截图 / 成就）
- 进度条增加剩余时间（ETA）估计

---

## 更新日志

最近几条（完整记录见 [`docs/CHANGELOG.md`](docs/CHANGELOG.md)）：

### 0.4.14

- 终端启动信息改为「整段英文 + 整段中文」，不再逐行穿插。
- 补齐网页英文遗漏：「下载画质」各预设、缓存统计（`Cache: Download …`）、
  「未知应用」→ `Unknown app`。

### 0.4.13

- 国际化：网页端顶栏「EN / 中文」切换（默认跟随浏览器语言）、`serve` 启动信息中英双语、
  Decky 插件跟随系统语言；修正英文界面下手机顶栏重叠。

### 0.4.12

- 游戏详情多选支持「全选」（仅游戏内，「全部」视图不重复提供）。

### 0.4.11

- 截图 ZIP 改为**平铺**：`时间.jpg`，不再按游戏名建子文件夹。

### 0.4.10

- 多选新增「打包选中」；移除工具栏里与侧栏重复的「打包下载」按钮。

---

## 许可

MIT
