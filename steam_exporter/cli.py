"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .exporter import (
    DEFAULT_TEMPLATE,
    MODE_COMPRESS,
    MODE_LOSSLESS,
    SOFTWARE_CODECS,
    VAAPI_CODECS,
    ExportOptions,
    Exporter,
    find_vaapi_device,
)
from .library import (
    TYPE_CHOICES,
    Filters,
    build_resolver,
    collect_sources,
    filter_sources,
    sort_sources,
)
from .model import KIND_BACKGROUND, KIND_CLIP, KIND_FOREGROUND
from .steam import find_steam_roots, find_userdata_dirs, primary_user
from .util import find_tool, human_duration, human_size, raise_nofile_limit


# ---------------------------------------------------------------------------
# 参数 -> 过滤器
# ---------------------------------------------------------------------------
def filters_from_args(args) -> Filters:
    return Filters(
        type=getattr(args, "type", "all"),
        games=getattr(args, "game", None) or [],
        since=getattr(args, "since", None),
        until=getattr(args, "until", None),
        ids=getattr(args, "ids", None) or [],
    )


def has_selector(args) -> bool:
    return bool(getattr(args, "all", False)) or filters_from_args(args).is_active()


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------
def cmd_doctor(args) -> int:
    print(f"steam_exporter {__version__}")
    print()
    print("== 依赖 ==")
    ffmpeg = find_tool("ffmpeg")
    ffprobe = find_tool("ffprobe")
    print(f"  ffmpeg : {ffmpeg or '未找到 ❌'}")
    print(f"  ffprobe: {ffprobe or '未找到（可选）'}")
    if not ffmpeg:
        return 1
    print()
    print("== Steam 目录 ==")
    roots = find_steam_roots()
    for root in roots:
        print(f"  {root}")
    if not roots:
        print("  未找到 Steam 安装目录 ❌")
        return 1
    dirs = find_userdata_dirs()
    print()
    print("== 录制账号 ==")
    if not dirs:
        print("  未找到任何 gamerecordings ❌")
    for d in dirs:
        clips = d / "gamerecordings" / "clips"
        count = sum(1 for _ in clips.iterdir()) if clips.is_dir() else 0
        marker = " (主账号)" if d.name == primary_user() else ""
        print(f"  {d.name}: {count} 个剪辑{marker}")
    print()
    print("== 可用编码器（压缩模式）==")
    print(f"  软件: {', '.join(sorted(SOFTWARE_CODECS))}")
    device = find_vaapi_device()
    print(f"  硬件 VAAPI: {', '.join(sorted(VAAPI_CODECS))}（设备：{device or '未找到'}）")
    return 0


def cmd_users(args) -> int:
    dirs = find_userdata_dirs()
    if not dirs:
        print("未找到任何录制账号。")
        return 1
    main = primary_user()
    for d in dirs:
        clips = d / "gamerecordings" / "clips"
        videos = d / "gamerecordings" / "video"
        clip_count = sum(1 for _ in clips.iterdir()) if clips.is_dir() else 0
        video_count = sum(1 for _ in videos.iterdir()) if videos.is_dir() else 0
        marker = "  <- 主账号" if d.name == main else ""
        print(f"{d.name}  剪辑 {clip_count}  录像会话 {video_count}{marker}")
    return 0


def cmd_list(args) -> int:
    resolver = build_resolver()
    sources = collect_sources(getattr(args, "user", None), resolver)
    sources = filter_sources(sources, filters_from_args(args))
    sources = sort_sources(sources, newest_first=not getattr(args, "reverse", False))
    if not sources:
        print("没有匹配的录制。")
        return 0

    if getattr(args, "json", False):
        print(
            json.dumps(
                [s.to_dict(include_parts=True) for s in sources],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    rows = [
        [
            s.kind_cn,
            s.game,
            s.recorded_at.astimezone().strftime("%Y-%m-%d %H:%M"),
            human_duration(s.duration_s),
            human_size(s.size_bytes),
            s.source_id,
        ]
        for s in sources
    ]
    headers = ["类型", "游戏", "录制时间", "时长", "大小", "ID"]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], _display_width(cell))

    print(_format_row(headers, widths))
    print("-" * (sum(widths) + 2 * (len(widths) - 1)))
    for row in rows:
        print(_format_row(row, widths))

    total_size = sum(s.size_bytes for s in sources)
    print()
    print(
        f"共 {len(sources)} 条  合计 {human_size(total_size)}"
        f"（其中剪辑 {sum(1 for s in sources if s.kind == KIND_CLIP)}，"
        f"后台 {sum(1 for s in sources if s.kind == KIND_BACKGROUND)}，"
        f"前台 {sum(1 for s in sources if s.kind == KIND_FOREGROUND)}）"
    )
    return 0


def _display_width(text: str) -> int:
    """粗略按东亚宽度估算显示宽度。"""
    width = 0
    for ch in text:
        width += 2 if ord(ch) > 0x2E7F else 1
    return width


def _format_row(cells: list[str], widths: list[int]) -> str:
    parts = []
    for cell, width in zip(cells, widths):
        pad = width - _display_width(cell)
        parts.append(cell + " " * max(0, pad))
    return "  ".join(parts)


def cmd_export(args) -> int:
    if not has_selector(args):
        print("请先指定要导出的范围，例如 --all、--game 关键词、--type clip，")
        print("或给出剪辑 ID。为防止误导出全部数据，这里不做默认全量导出。")
        return 2

    resolver = build_resolver()
    sources = collect_sources(getattr(args, "user", None), resolver)
    sources = filter_sources(sources, filters_from_args(args))
    if not sources:
        print("没有匹配的录制。")
        return 0
    # 按录制时间升序，--limit 才有可预期的含义
    sources = sort_sources(sources, newest_first=False)
    if args.limit:
        sources = sources[: args.limit]

    if args.mode == MODE_COMPRESS and args.codec in VAAPI_CODECS:
        device = find_vaapi_device()
        if not device:
            print("未找到 VAAPI 设备，无法使用硬件编码。可改用 libx264。")
            return 2
        args.vaapi_device = device
    else:
        args.vaapi_device = None

    out_dir = Path(args.out).expanduser()
    options = ExportOptions(
        out_dir=out_dir,
        mode=args.mode,
        template=args.template,
        crf=args.crf,
        preset=args.preset,
        codec=args.codec,
        audio_bitrate=args.audio_bitrate,
        scale=args.scale,
        faststart=not args.no_faststart,
        force=args.force,
        dry_run=args.dry_run,
        jobs=max(1, args.jobs),
        use_manifest=not args.no_manifest,
        vaapi_device=args.vaapi_device,
        tmp_dir=Path(args.tmp).expanduser() if args.tmp else None,
        trim=not args.no_trim,
    )

    print(
        f"模式：{'无损复制' if args.mode == MODE_LOSSLESS else '压缩转码'}"
        f"  输出目录：{out_dir}"
    )
    if args.mode == MODE_COMPRESS:
        print(
            f"编码器：{args.codec}  CRF/QP：{args.crf}"
            + (f"  缩放：{args.scale}" if args.scale else "")
        )

    exporter = Exporter(options)
    stats = exporter.export(sources)

    print()
    if args.dry_run:
        print(f"[dry-run] 计划导出 {stats.planned} 条。")
    else:
        print(
            f"完成：成功 {stats.exported}，跳过 {stats.skipped}，失败 {stats.failed}，"
            f"输出 {human_size(stats.bytes)}"
        )
    return 1 if stats.failed else 0


def cmd_serve(args) -> int:
    from .server import serve

    return serve(
        host=args.host,
        port=args.port,
        open_browser=args.open_browser,
        token=args.token,
        default_user=args.user,
        default_out=Path(args.out).expanduser(),
        cache_dir=Path(args.cache).expanduser() if args.cache else None,
        cache_ttl=max(0.0, args.cache_ttl) * 60,
        cache_max=max(1, int(args.cache_max * 1024**3)),
    )


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------
def _parse_date(text: str):
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"无法解析日期：{text}（应形如 2026-09-01）")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="steamexport",
        description="从 SteamOS / Steam 游戏录制中导出剪辑与录像。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    # doctor
    p_doc = sub.add_parser("doctor", help="检查环境与依赖")
    p_doc.set_defaults(func=cmd_doctor)

    # users
    p_users = sub.add_parser("users", help="列出有录制的账号")
    p_users.set_defaults(func=cmd_users)

    # 过滤参数（list / export 共用）
    def add_filters(p):
        p.add_argument("--user", default="auto",
                       help="账号：auto（默认，主账号）/ all / SteamID")
        p.add_argument("--type", choices=TYPE_CHOICES, default="all",
                       help="录制类型过滤")
        p.add_argument("--game", action="append", metavar="关键词",
                       help="按游戏名包含的关键词过滤（可多次）")
        p.add_argument("--since", type=_parse_date, help="起始日期（含）")
        p.add_argument("--until", type=_parse_date, help="结束日期（含）")

    # list
    p_list = sub.add_parser("list", help="列出录制（预览）")
    add_filters(p_list)
    p_list.add_argument("ids", nargs="*", help="按 ID 或 ID 片段过滤")
    p_list.add_argument("--json", action="store_true", help="输出 JSON")
    p_list.add_argument("--reverse", action="store_true", help="升序排列（默认降序）")
    p_list.set_defaults(func=cmd_list)

    # export
    p_exp = sub.add_parser("export", help="导出录制")
    add_filters(p_exp)
    p_exp.add_argument("ids", nargs="*", help="按 ID 或 ID 片段过滤")
    p_exp.add_argument("--all", action="store_true", help="导出所有匹配项")
    p_exp.add_argument("-o", "--out", default="~/Videos/SteamExports",
                       help="输出目录（默认 ~/Videos/SteamExports）")
    p_exp.add_argument("--mode", choices=[MODE_LOSSLESS, MODE_COMPRESS],
                       default=MODE_LOSSLESS, help="无损复制（默认）或压缩转码")
    p_exp.add_argument("--codec", default="libx264",
                       help="压缩模式编码器：libx264 / libx265 / libsvtav1 / h264_vaapi …")
    p_exp.add_argument("--crf", type=int, default=23,
                       help="压缩质量（越小越清晰，默认 23；VAAPI 用 QP）")
    p_exp.add_argument("--preset", default="medium",
                       help="x264/x265 速度预设（默认 medium）")
    p_exp.add_argument("--audio-bitrate", default="160k", help="压缩音频码率")
    p_exp.add_argument("--scale", default=None,
                       help="压缩时缩放，如 1280:-2（默认不缩放）")
    p_exp.add_argument("--template", default=DEFAULT_TEMPLATE,
                       help="输出命名模板，默认 " + DEFAULT_TEMPLATE)
    p_exp.add_argument("--no-faststart", action="store_true",
                       help="不写 faststart（默认写入，便于发布/流式播放）")
    p_exp.add_argument("--force", action="store_true", help="忽略清单，强制重新导出")
    p_exp.add_argument("--dry-run", action="store_true", help="只显示计划，不实际导出")
    p_exp.add_argument("-j", "--jobs", type=int, default=1, help="并发导出数（默认 1）")
    p_exp.add_argument("--limit", type=int, default=None,
                       help="按录制时间升序只导出前 N 条（便于分批）")
    p_exp.add_argument("--tmp", default=None,
                       help="存放临时符号链接/合并文件的目录（默认输出目录）")
    p_exp.add_argument("--no-trim", action="store_true",
                       help="不按元数据时长裁剪尾部（默认会裁掉多余的不完整分段）")
    p_exp.add_argument("--no-manifest", action="store_true", help="不写导出清单")
    p_exp.set_defaults(func=cmd_export)

    # serve
    p_serve = sub.add_parser("serve", help="启动网页管理界面")
    p_serve.add_argument("--host", default="127.0.0.1",
                         help="监听地址（默认仅本机 127.0.0.1）")
    p_serve.add_argument("--port", type=int, default=8787, help="监听端口（默认 8787）")
    p_serve.add_argument("--open", dest="open_browser", action="store_true", default=True,
                         help="启动后自动打开浏览器（默认）")
    p_serve.add_argument("--no-open", dest="open_browser", action="store_false",
                         help="启动后不自动打开浏览器")
    p_serve.add_argument("--token", default=None,
                         help="访问令牌；设置后访问需带 ?token=xxx")
    p_serve.add_argument("--user", default="auto",
                         help="界面默认账号：auto / all / SteamID")
    p_serve.add_argument("-o", "--out", default="~/Videos/SteamExports",
                         help="界面默认输出目录")
    p_serve.add_argument("--cache", default=None,
                         help="下载临时缓存目录（默认 ~/.cache/steam-exporter/downloads）")
    p_serve.add_argument("--cache-ttl", type=float, default=10,
                         help="下载缓存保留时间（分钟，默认 10）")
    p_serve.add_argument("--cache-max", type=float, default=5,
                         help="下载缓存容量上限（GB，默认 5）")
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    # 长录像的 concat 会同时打开上千个分片，先把 fd 软上限提到硬上限
    raise_nofile_limit()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
