# Steam 游戏录制存储格式

本文记录本工具对 Steam / SteamOS「游戏录制（Game Recording）」存储格式的实测结果，
供维护和二次开发参考。结论来自 Steam Deck 上的真实数据。

## 1. 存储位置

```
~/.local/share/Steam/userdata/<SteamID64>/gamerecordings/
```

Flatpak 版 Steam 在 `~/.var/app/com.valvesoftware.Steam/...`，本工具会自动探测常见路径。

## 2. 目录结构

```
gamerecordings/
├── clips/
│   └── clip_<gameid>_<YYYYMMDD>_<HHMMSS>/
│       ├── clip.pb            # protobuf 元数据
│       ├── thumbnail.jpg      # 缩略图
│       ├── timelines/
│       │   └── timeline_*.json
│       └── video/
│           └── <session>/     # 自包含的 DASH 分片（剪辑副本）
├── video/
│   ├── fg_<gameid>_<YYYYMMDD>_<HHMMSS>/       # 前台录制
│   ├── bg_<gameid>_<YYYYMMDD>_<HHMMSS>/       # 后台（滚动缓冲）
│   └── bg_<gameid>_<YYYYMMDD>_<HHMMSS>_0/     # 同一条录像的续写段
├── timelines/
│   └── timeline_*.json        # 事件（截图、成就等）
└── gamerecording.pb           # 录制总索引
```

### 会话目录命名

```
<prefix>_<gameid>_<YYYYMMDD>_<HHMMSS>[_<续段号>]
```

- `prefix`：`fg`=前台，`bg`=后台
- **目录名的时间是 UTC**（不是本地时间）
- 续段号 `_0`、`_1` …：同一条录像因为体积/文件上限被 Steam 写到新目录，
  **chunk 编号会从 1 重新开始**，且每个续段目录都有自己的 `init` 头。

## 3. 视频格式：MPEG-DASH（fMP4）

每个会话目录：

```
init-stream0.m4s              # 视频初始化段（ftyp + moov）
init-stream1.m4s              # 音频初始化段
chunk-stream0-00001.m4s ...   # 视频分片（命名会按 %05d 补零）
chunk-stream1-00001.m4s ...
session.mpd                   # DASH 清单
```

- 视频：H.264 High（`avc1.640c20`），常见 1280×800 / 1280×720，约 6.6 Mbps
- 音频：AAC-LC（`mp4a.40.2`），48 kHz 立体声
- 单段时长：`session.mpd` 里 `SegmentTemplate duration="3000000" timescale="1000000"` → 3 秒

### 拼接算法

`init + 按编号排序的 chunk` 逐字节拼接就是一个可解析的 fMP4 流。本工具用 ffmpeg 的
`concat:` 协议一次完成，无需临时文件：

```
ffmpeg -i "concat:init-stream0.m4s|chunk-stream0-00001.m4s|..." \
       -i "concat:init-stream1.m4s|chunk-stream1-00001.m4s|..." \
       -map 0:v:0 -map 1:a:0 -c copy out.mp4
```

**续段的处理**：由于各续段的 `init` 内容不同（实测 md5 不一致），不能简单地把续段的
chunk 接到第一段 init 后面。可以继续在同一个 `concat:` 串里，按顺序插入每一段自己的
`init` 与 chunk：

```
concat: base/init0 | base/chunk0-* | _0/init0 | _0/chunk0-* | _1/init0 | ...
```

实测（`video/bg_204360_20261014_155159` + `_0`）可正确得到 768 秒、含音视频的完整文件。

### 长录像与单参数长度上限

`concat:` 协议把文件列表拼成**一个命令行参数**。Linux 对单个参数有 128 KiB 上限
（`MAX_ARG_STRLEN`），一个 51 分钟的剪辑约 1024 个分片、路径拼接后约 154 KB，
会报 `[Errno 7] Argument list too long`。

解决办法：在临时目录建立一批**短名符号链接**（如 `stream0/00000.m4s`）指向真实分片，
用短路径重拼 `concat:`，参数长度降到约 27 KB，无需复制数据。若分片数量极端多
（链接路径仍超限），则逐字节合并成临时文件。

### 文件描述符上限

ffmpeg 的 `concat:` 协议会**同时打开列表中的所有文件**，因此分片数还受进程 fd
软上限（`RLIMIT_NOFILE`）限制。SteamOS 的 systemd 默认为
`DefaultLimitNOFILESoft=1024`、硬上限 `524288`，在终端里直接跑会报
`Too many open files`。

本工具启动时把软上限提升到硬上限（普通用户即可，无需特权）作为首选方案；若硬上限
本身不足以容纳分片，则逐字节合并为单个临时文件后只用单文件输入。

## 4. 滚动缓冲（后台录像）

后台录像的 `session.mpd` 常见：

```xml
<MPD type="static" timeShiftBufferDepth="PT1H0M0.0S"
     maxSegmentDuration="PT3.0S" mediaPresentationDuration="PT0.0S">
```

- `mediaPresentationDuration` 为 `PT0.0S`，即**不能用 mpd 直接得到时长**
- `timeShiftBufferDepth` 表示只保留最近约 1 小时
- 现存分片编号可能从中间开始（如 `chunk-stream0-00315.m4s`），前面的已被删除

因此时长估算为：`现存视频分片数 × SegmentTemplate 单段时长`。
本工具在 `util.session_duration_seconds()` 中实现：mpd 时长 > 0.5s 时直接用，
否则回退到分片估算。

## 5. `clip.pb` 字段

用 `protoc --decode_raw` 观察到的结构（字段号 → 含义）：

```
1  {                          # 源引用
     1: "timeline_103030020250905_134153"   # 对应 timeline 文件名
     2: 1030300                             # gameid
     3: 1757079713                          # 源录像“会话开始”unix 时间（注意不是剪辑时刻！）
     4: 17730                               # 相关时长
     5 {                                     # 源视频会话
         1: "fg_1030300_20250905_150551"     # 会话目录名
         2: 375
         3: 17355                            # 时长（毫秒）
         4: 4
        10: 5038616                          # 相关体积
     }
}
2: 5038241                     # 相关体积（源片段）
3: 1757084751                  # 其它时间戳
4: 1030300                     # gameid
6: 15098241
7: "Outer Wilds - 2026-09-05 17:21:57"   # 可选：名称（本地时间）
8: 0
12: 1280                      # 宽
13: 800                       # 高
```

**关键结论**：

- 判定剪辑时刻用**剪辑目录名**（`clip_<gameid>_<日期>_<时间>`，UTC），
  不要用字段 `1.3`——那是源录像会话的开始时间。
- 游戏名不在此文件里，需要外部解析（见第 6 节）。
- 字段 `7` 是 Steam 记录的名称（本地时间），并非所有剪辑都有。

## 6. 游戏名解析

`gameid` 与 Steam AppID 的关系：

- 普通 Steam 游戏：`gameid == appid`
- 非 Steam 快捷方式：`gameid = (appid << 32) | 0x02000000`
  （低 32 位恒为 `0x02000000`，`appid = gameid >> 32`）

名称来源：

1. 所有库目录下的 `appmanifest_<appid>.acf` → `"name"`
2. 非 Steam 快捷方式 `userdata/<id>/config/shortcuts.vdf` → `AppName`
3. 都找不到时，回退为「未知应用 <appid>」

库目录通过 `steamapps/libraryfolders.vdf` 枚举（可能包含 SD 卡等外置盘）。

`shortcuts.vdf` 是 Valve **二进制 VDF**，类型标记：`0x00` 嵌套开始、`0x01` 字符串、
`0x02` int32、`0x07` uint32、`0x08` 嵌套结束。

未实现但可扩展：解析 `appcache/appinfo.vdf` 以覆盖已卸载且已删快捷方式的游戏。

## 7. 时间戳与时区

- 目录名：UTC
- `clip.pb` 字段 `1.3`：unix 秒（会话开始）
- 展示与命名：本工具换算为系统本地时区
- 文件 mtime 可能因 SteamOS RTC 显示成未来时间，**不可依赖**

## 8. `gamerecording.pb`

顶层为重复字段 `1`，每条：

```
1 {
  1: "timeline_..."   # timeline 文件名
  2: <gameid>
  3: <unix 时间>
  4: <时长/相关值>
  7 { ... }           # 可选：名称等（仅部分条目有）
}
```

本工具目前不依赖它（直接扫描文件系统更完整），保留为后续补充元数据的来源。

## 9. 磁盘占用提示

剪辑目录里的视频是**独立副本**（与 `video/` 下的是不同 inode），不是硬链接。
因此「导出全部」会再复制一份等量数据，规划空间时按原始体积的 1 倍预留。
