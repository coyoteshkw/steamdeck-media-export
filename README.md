# steam-recording-exporter

从 **SteamOS / Steam 游戏录制（Game Recording）** 中批量导出「剪辑」与「录像」的小工具。

- 支持**无损导出**（`-c copy`，画质零损失、速度接近磁盘拷贝）
- 支持**压缩导出**（libx264 / libx265 / SVT-AV1 / VAAPI 硬件编码，自定义 CRF、缩放）
- 一键导出全部，或按游戏 / 类型 / 时间 / ID 精确挑选
- 自动解析游戏名（含非 Steam 快捷方式）、录制时间、时长、大小
- 自动合并 Steam 分段续写的后台录像（`xxx_0`、`xxx_1` …）
- 清单去重：重复运行只导出新增内容
- 只依赖 **Python 标准库 + 系统 ffmpeg**，无需安装第三方包

> 目标平台是 Steam Deck / SteamOS，但在任何有 Steam + ffmpeg 的 Linux 上都能用。

---

## 快速开始

### 环境要求

- Python ≥ 3.10
- `ffmpeg`（SteamOS 自带；其他系统 `sudo apt install ffmpeg`）

检查环境：

```bash
./bin/steamexport doctor
```

输出会列出 ffmpeg、Steam 目录、各账号的录制数量，以及可用的编码器。

### 只看不导出：列出录制

```bash
./bin/steamexport users                 # 有哪些账号、各有多少录制
./bin/steamexport list                  # 列出（默认当前主账号）
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

# 无损，导出全部（含后台录像）
./bin/steamexport export --all -o /run/media/deck/MyDisk/SteamExports

# 压缩导出（更小，适合发布）
./bin/steamexport export --game "Outer Wilds" --type clip --all \
    --mode compress --codec libx264 --crf 23 --preset veryfast

# 先预览计划，不实际写文件
./bin/steamexport export --all --dry-run

# 并发导出（Steam Deck 8 核，无损可开 4）
./bin/steamexport export --type clip --all -j 4

# 分批导出（按时间升序前 50 条）
./bin/steamexport export --type clip --all --limit 50
```

> 为防止误操作，`export` **必须**指定范围（`--all` / `--game` / `--type` / `--since` / ID），不会默认全量导出。

---

## 网页管理界面

除了命令行，还提供一个本地网页界面（无需任何额外依赖，只用 Python 标准库）：

```bash
./bin/steamexport serve                 # 启动并自动打开浏览器（Firefox）
./bin/steamexport serve --port 9000     # 换端口
./bin/steamexport serve --no-open       # 不自动打开
./bin/steamexport serve --host 0.0.0.0 --token mysecret   # 局域网/手机访问
```

界面功能：

- **按游戏分组的层级列表**：每个游戏一行，**游戏名前显示 Steam 图标**；可展开查看该游戏下的
  剪辑 / 后台录像 / 前台录像；游戏左侧复选框可整组全选（半选状态表示部分选中）
- **两栏布局**：左侧为导出设置与筛选，右侧为列表与进度，窄屏自动上下堆叠
- 筛选（类型 / 游戏 / 日期 / ID 搜索）、排序（最近录制 / 体积 / 名称）
- 勾选若干条或整个游戏后「导出选中」，或「导出全部匹配」
- 无损 / 压缩、编码器、CRF、并发、命名模板等设置直接在页面上调
- 「仅预演」先看计划和预计体积，不写文件
- **实时进度**（SSE 推送）：整体进度 + 当前条目百分比 + 速度；每个游戏行显示完成/失败汇总
- 导出中途可「取消任务」，会自动清理半成品文件
- **下载到本机**：每条录制右侧有下载按钮，直接把内容下到手机/电脑
  - 独立「下载画质」选择：原画无损（流式、瞬时）/ 快速(veryfast) / 均衡(medium) /
    小体积(720p) / 硬件(VAAPI、低 CPU)
  - 无损走**流式**（不落盘）；压缩先编码到**临时缓存**，再以普通 MP4 +
    `Content-Length` + `Range` 提供下载（**可断点续传**，手机网络不稳也能续）
  - **不会在 Steam Deck 的导出目录生成文件、也不写清单**；缓存按时长（默认 30 分钟）/容量
    （默认 5GB，LRU）自动清理，可手动「清理缓存」
  - 同一设置短期内重复下载直接命中缓存，不再重编码
- 「打开导出目录」直接调 `xdg-open`

> 默认只监听 `127.0.0.1`（仅本机）。要远程访问，务必加 `--token`。

### 局域网访问（手机 / 平板 / 另一台电脑）

让别的设备也能访问（需在**同一个 Wi-Fi / 局域网**）：

```bash
./bin/steamexport serve --host 0.0.0.0 --token 你的口令
```

启动后会直接打印可访问地址：

```
  局域网地址（手机 / 平板 / 另一台电脑都可访问）：
    http://192.168.0.103:8787/?token=你的口令
```

在手机浏览器里打开该地址即可；手机上面面会自动变为**单栏**布局，按钮、间距、
底部进度栏都做了适配。

注意事项：

- **一定要加 `--token`**，否则同网段任何人都能操作（删除/导出）；
- 只在可信的家庭局域网使用，**不要暴露到公网**（只有口令一道防护）；
- CPU 和磁盘都在 Steam Deck 上，“导出”仍然在 Deck 本地读写；
- 「打开导出目录」是在 **Steam Deck** 上执行 `xdg-open`，手机端只是触发。
- 若连不上，检查：手机与 Deck 同一网段、Steam Deck 防火墙是否放行该端口、
  以及 `--port` 是否被占用。

后端接口（也可供脚本/未来 Decky 插件直接调用）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/summary` | 账号、ffmpeg、编码器、默认设置 |
| GET | `/api/recordings` | 录制列表（支持筛选参数） |
| GET | `/api/download` | 无损下载（流式，不在 Deck 落盘） |
| POST | `/api/download/prepare` | 准备压缩下载（命中缓存或起编码任务） |
| GET | `/api/download/file` | 下载缓存文件（支持 Range 续传） |
| POST | `/api/cache/clear` | 清空下载缓存 |
| GET | `/api/icon/{appid}` | 游戏图标（Steam 本地缓存） |
| POST | `/api/export` | 创建导出任务（`dry_run` 时直接返回计划） |
| GET | `/api/jobs/{id}` | 任务状态快照 |
| GET | `/api/jobs/{id}/events` | SSE 进度事件流 |
| POST | `/api/jobs/{id}/cancel` | 取消任务 |
| POST | `/api/reveal` | 在文件管理器中打开目录 |

---

## 命令详解

### `export` 常用参数

| 参数 | 说明 |
|---|---|
| `--all` | 导出所有匹配项 |
| `--game 关键词` | 按游戏名过滤，可多次 |
| `--type clip\|background\|foreground\|recording\|all` | 类型过滤（`recording` = 后台+前台） |
| `--since / --until YYYY-MM-DD` | 录制日期范围（按本地时区） |
| `ids`（位置参数） | 按 ID 或 ID 片段过滤，如 `clip_753640_20260905_092200` |
| `--user auto\|all\|SteamID` | 账号，默认 `auto`=主账号 |
| `-o, --out DIR` | 输出目录，默认 `~/Videos/SteamExports` |
| `--mode lossless\|compress` | 无损（默认）或压缩 |
| `--codec` | 压缩编码器：`libx264`（默认）/`libx265`/`libsvtav1`/`h264_vaapi`/`hevc_vaapi`/`av1_vaapi` |
| `--crf` | 压缩质量，越小越清晰，默认 23（VAAPI 用 `-qp`） |
| `--preset` | x264/x265 速度预设，默认 `medium` |
| `--scale 1280:-2` | 压缩时缩放（等比例） |
| `--audio-bitrate` | 压缩音频码率，默认 `160k` |
| `--template` | 输出命名模板（见下） |
| `--no-faststart` | 不写 faststart（默认写入，便于发布/流式播放） |
| `--tmp DIR` | 存放临时符号链接/合并文件的目录（默认输出目录） |
| `--no-trim` | 不按元数据时长裁剪尾部（默认会裁掉多余的不完整分段） |
| `--force` | 忽略清单，强制重新导出 |
| `--dry-run` | 只显示计划 |
| `-j, --jobs N` | 并发导出数 |
| `--no-manifest` | 不写去重清单 |

### 命名模板

默认：`{game}/{date} {time} - {kind_cn}.mp4`
例：`Outer Wilds/2026-09-05 17-22-00 - 剪辑.mp4`

可用变量：

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

变量值里的路径分隔符会被自动替换成 `_`；`/` 只在模板里用于建子目录。模板生成的路径不能越出输出目录。

示例：

```bash
--template "{game}/{date}/{time} - {kind_cn}.mp4"
--template "Steam录制/{date} {time} {game}.mp4"
```

### 去重清单

每次导出会在输出目录写 `.steam_exporter_manifest.json`，记录已导出的源。
再次运行时，已存在且清单中命中的会被跳过；要强制重导用 `--force`，要禁用清单用 `--no-manifest`。

---

## 无损 vs 压缩

| | 无损（`lossless`） | 压缩（`compress`） |
|---|---|---|
| 原理 | ffmpeg `-c copy` 重新封装 | 重新编码 |
| 画质 | 与录制完全一致 | 由 `--crf`/`--preset` 决定 |
| 速度 | 极快（≈ 磁盘拷贝） | 慢，取决于 CPU/GPU |
| 体积 | 与原始一致（约 6.6 Mbps） | 可小到 1/5 ~ 1/10 |
| 推荐场景 | 存档 | 发布、分享、省空间 |

压缩示例（软件）：

```bash
./bin/steamexport export --all --mode compress --codec libx264 --crf 20 --preset slow
./bin/steamexport export --all --mode compress --codec libx265 --crf 24 --preset medium
./bin/steamexport export --all --mode compress --codec libsvtav1 --crf 30 --preset 6
```

压缩示例（VAAPI 硬件编码，Steam Deck 上更快、更省电）：

```bash
./bin/steamexport export --all --mode compress --codec h264_vaapi --crf 24
```

> 硬件编码的 `--crf` 实际传给 `-qp`，质量范围与软件编码不完全等价，需要自行多试几次。

---

## 输出目录

```
~/Videos/SteamExports/
├── .steam_exporter_manifest.json     # 去重清单
├── Outer Wilds/
│   ├── 2026-08-22 22-44-15 - 剪辑.mp4
│   └── 2026-10-04 21-50-16 - 剪辑.mp4
└── Castle Crashers/
    └── 2026-10-14 23-51-59 - 后台录像.mp4
```

---

## 进度显示

单条导出（默认 `-j 1`）时会实时显示百分比、已处理/总时长与速度：

```
[1/1] 进度  73%  0:33/0:45  1.2x
```

终端下原地刷新；重定向到文件时按整数百分比换行输出。
多并发（`-j > 1`）时不显示逐条进度，只在每条完成时输出一行。

## 长录像（Argument list too long / Too many open files）

Steam 录像是 DASH 分片，一个 51 分钟的剪辑就有上千个分片，会撞击两个系统限制：

1. **单个命令行参数 128 KiB 上限**（`MAX_ARG_STRLEN`）：把所有分片路径拼成一个
   `concat:` 参数会报 `OSError: [Errno 7] Argument list too long`。
   解决：改用**短名符号链接**（不复制数据），把参数降到阈值以下。

2. **文件描述符上限**：ffmpeg 的 `concat:` 协议会**同时打开所有分片**，
   而很多终端下 fd 软上限只有 1024（systemd 的 `DefaultLimitNOFILESoft`），
   会报 `Too many open files`。解决：程序启动时自动把软上限提升到硬上限
   （SteamOS 上硬上限通常为 524288，普通用户即可提升）；若硬上限本身也很低，
   则回退到把分片逐字节合并成一个临时文件，再用单文件输入。

可用 `--tmp DIR` 指定临时文件目录（默认为输出目录，避免写到内存盘）。

## 注意事项与已知限制

1. **导出时最好别正在录制**：录制进行中文件会被写入，最好先停止游戏/录制，或关闭 Steam。
2. **时间戳**：Steam 目录名用的是 **UTC**，本工具已换算成本地时间显示与命名。
3. **后台录像是滚动缓冲**：Steam 只保留最近一段时间（默认约 1 小时，见 mpd 的
   `timeShiftBufferDepth`），更早的分片会被自动删除，无法找回。列表里的时长对这类
   录像为估算值（现存分片数 × 单段时长）。
4. **文件修改时间不可信**：SteamOS 上 mtime 可能显示成未来时间，本工具一律使用目录名/元数据里的时间。
5. **空间**：无损导出等于复制一份。你的账号约 54 GB 录制，导出前请确认目标盘剩余空间；
   用 `--dry-run` 可先看预计体积。
6. **游戏名解析**：优先用已安装游戏的 `appmanifest_*.acf` 与非 Steam 快捷方式
   `shortcuts.vdf`；已卸载且快捷方式也被删掉的会显示为「未知应用 <appid>」，可用
   `--template` 自行组织命名。
7. **多账号**：`--user` 默认只处理主账号（`loginusers.vdf` 里的 `MostRecent`）；
   要处理全部账号用 `--user all`。
8. **损坏录制自动恢复**：个别录制的视频头部（`init-stream0.m4s`）可能为 0 字节
   （Steam 写入异常）。工具会跳过空文件，并尝试借用其它**同分辨率**录制的头部
   做 best-effort 恢复并打印告警；若无法恢复，会明确提示并跳过该条，不影响其余导出。

---

## 开发

```bash
# 运行测试
python3 -m unittest discover -s tests -v

# 直接以模块方式运行（等价于 ./bin/steamexport）
python3 -m steam_exporter --help
```

### 项目结构

```
steam_exporter/
├── cli.py        # 命令行入口、过滤与命令实现
├── server.py     # 本地 HTTP 服务 + 网页后端（API / SSE）
├── web/          # 网页前端（index.html / app.js / style.css，无构建步骤）
├── library.py    # 账号选择、扫描、过滤（CLI 与网页共用）
├── scanner.py    # 扫描 gamerecordings，解析目录名与 clip.pb
├── steam.py      # Steam 目录发现、VDF 解析、游戏名解析
├── exporter.py   # ffmpeg 命令构建、命名模板、清单去重、并发、事件回调
├── model.py      # Source 数据模型
├── pb.py         # 极简 protobuf 解析
└── util.py       # 格式化、mpd 时长、ffmpeg 调用等辅助
docs/format.md    # Steam 录制存储格式说明
tests/            # 单元测试
bin/steamexport   # 便捷启动脚本
```

详见 [`docs/format.md`](docs/format.md)。

---

## 后续计划

- ~~图形界面~~ → 已完成**网页管理界面**（`steamexport serve`）
- Decky Loader 插件（游戏模式内一键操作，可直接复用现有 `/api/*`）
- 导出时可选同时导出缩略图与时间线事件（截图/成就）
- 更完整的游戏名解析（解析 `appinfo.vdf` 覆盖已卸载游戏）
- 进度条增加剩余时间（ETA）估计

## 更新日志

### 0.2.7（2026-10-15）

- **下载到本机重构**（针对“压缩下载慢”）：
  - 新增独立的「下载画质」预设：原画无损 / 快速(veryfast) / 均衡(medium) / 小体积(720p) / 硬件(VAAPI)
  - 无损仍走流式（瞬时）；压缩改为**先编码到临时缓存，再以普通 MP4 下载**：
    - 带 `Content-Length`，手机端有真实下载进度
    - 支持 `Range`，**断点续传**
    - 输出为 faststart 普通 MP4，比流式的分片 MP4 更兼容手机相册/剪辑 App
  - 缓存**不进导出目录、不写清单**，按时长（默认 30 分钟）+ 容量（默认 5GB，LRU）自动清理，
    可在界面「清理缓存」；`serve` 新增 `--cache / --cache-ttl / --cache-max`
  - 实测 7.6MB 剪辑 fast 压缩后 1.96MB，命中缓存不重编
- 说明：压缩耗时主要在**编码**，不在这传输（实测无损 remux 186× 实时，
  `medium` 仅 1.5×、`veryfast` 3.4×、硬件 5–8×），因此下载默认用快速/硬件预设
- 新增模块 `download_cache.py`；单元测试增至 62 项

### 0.2.6（2026-10-15）

- **新增「下载到本机」**：每条录制右侧按钮，可把内容直接下到手机/电脑
  - 复用当前「模式」设置：无损或压缩；无损用 `-c copy`，压缩实时转码
  - **不在 Steam Deck 落盘**：ffmpeg 输出分片 MP4 到 pipe，边转边发，
    不写入导出目录，也不记录到导出清单（避免多端存储）
  - `Content-Disposition` 带 UTF-8 文件名，手机浏览器直接保存
- **手机端列表可横向滑动**：不再隐藏列，左右滑动即可看完整信息（ID / 时长等），
  下载按钮 `position: sticky` 固定在右侧随时可点
- **修复「后台录像」标签在手机端溢出**：加宽类型列并限制溢出，不再遮挡日期
- 新增 `/api/download` 接口

### 0.2.5（2026-10-15）

- **手机端适配**：更窄的断点（≤560px），单栏布局、压缩的列表行、底部状态栏自动换行、
  安全区（刘海屏）适配；补充 `theme-color`、`color-scheme`
- **局域网访问更友好**：`serve --host 0.0.0.0` 启动时会直接打印可访问的局域网 URL；
  未设 token 时给出醒目警告
- README 新增「局域网访问」章节

### 0.2.4（2026-10-15）

- **导出进度改为底部固定状态栏**：不再占用列表空间，游戏列表全程可见
  - 紧凑高度（整体/当前两条进度 + 状态 + 按钮）
  - 日志默认收起，点「日志」展开；出错或预演时自动展开

### 0.2.3（2026-10-15）

- **网页界面美化与重排**：改为左侧设置/筛选栏 + 右侧列表的两栏布局，卡片式视觉
- **游戏图标**：游戏名前显示 Steam 本地缓存里的图（头部/胶囊/竖版/Hero/`logo.png`，
  非 Steam 快捷方式用 `config/grid/`），新增 `/api/icon/<appid>` 接口
  - 图标缺失时回退为首字母占位块
- 单元测试增加到 53 项

### 0.2.2（2026-10-15）

- **修复导出比实际录制多几秒**：Steam 剪辑按整段（默认 3 秒）保存，尾部常多出不满
  一段的余量（如 45 秒的剪辑存了 16 段 = 48 秒）。现在导出时按元数据时长
  （`session.mpd` / `clip.pb`，即 Steam 实际播放长度）裁掉尾部，与 Steam 一致。
  - 命令新增 `--no-trim`，网页新增「裁剪多余尾部」复选框（默认开启）
  - 实测 48.00s → 45.01s，与 mpd 的 45.0s 一致；多段后台录像不受影响
- 单元测试增加到 48 项

### 0.2.1（2026-10-15）

- **修复个别损坏录制导致导出中断**：有些录制的视频头部 `init-stream0.m4s` 为 0 字节，
  会让 ffmpeg 报 `no tfhd was found` / `invalid data found when processing input`
  - 自动跳过 0 字节的 init 段与分片
  - 检测到视频 init 丢失时，自动借用其它录制的**同分辨率**头部做 best-effort 恢复，并打印告警；
    实测可完整救回一条 48 秒的剪辑
  - 找不到匹配头部时给出明确错误并跳过该条，不影响整批导出
- 网页/命令行导出增加 `item_warning` 告警输出
- 单元测试增加到 45 项

### 0.2.0（2026-10-15）

- **新增网页管理界面** `steamexport serve`：本地 HTTP API + 单页前端，零额外依赖
  - 按游戏分组的层级列表（游戏级全选 / 展开看单个）、筛选/排序、导出选中/全部、预演（dry-run）、实时进度（SSE）、取消任务、打开导出目录
  - `/api/*` 接口设计为可供未来 Decky 插件复用
- **导出进度显示**：百分比 + 已处理/总时长 + 速度
- **修复长录像的两个系统限制**：
  - 单个命令行参数 128 KiB 上限（`Argument list too long`）→ 改用短名符号链接，不复制数据
  - 文件描述符软上限（`Too many open files`）→ 启动时提升到硬上限，极端情况合并为单文件
- **支持取消**进行中的导出（约 1 秒响应），并清理未完成的半成品文件
- 修正主账号识别（新版 `loginusers.vdf` 无 `MostRecent` 字段，且需 64→32 位换算）
- 抽出 `library.py`，命令行与网页共用账号选择 / 扫描 / 过滤逻辑
- 单元测试增加到 40 项

### 0.1.0（初版）

- 命令行导出：`list` / `export` / `users` / `doctor`
- 无损（`-c copy`）与压缩（libx264 / libx265 / SVT-AV1 / VAAPI）
- 命名模板、清单去重、并发导出、`--dry-run`
- 自动合并后台录像的续段（`_0`、`_1`…）
- 游戏名解析（`appmanifest_*.acf` / `shortcuts.vdf`），支持非 Steam 快捷方式
- 文档：`README.md` + `docs/format.md`

---

## 许可

MIT
