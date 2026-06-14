#!/usr/bin/env python3
"""
複数チャンネル録画の重複を整理するスクリプト
"""

import argparse
import os
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table
from rich.text import Text

from lib.recording_dedupe import (
    RecordingFileSet,
    build_recording_file_set,
    delete_recording_files,
    find_duplicate_groups,
    format_drop_rate,
    move_recording_safely,
    select_best_recording,
    summarize_group_title,
)


class ResultStatus(Enum):
    """処理結果ステータス"""

    KEPT = "保持"
    MOVED = "移動完了"
    DELETED = "削除完了"
    SKIPPED = "スキップ"
    ERROR = "エラー"
    DRY_RUN = "dry-run"


@dataclass
class FileResult:
    """録画ごとの処理結果"""

    group_name: str
    title: str
    service_name: str
    filename: str
    kept_filename: str
    drop_rate_text: str
    status: ResultStatus
    message: str = ""


def load_config_from_env() -> dict[str, object]:
    """.env ファイルから設定を読み込む"""
    load_dotenv()
    return {
        "target_dir": os.getenv("PRUNE_DUPLICATES_TARGET_DIR"),
        "destination_dir": os.getenv("PRUNE_DUPLICATES_DEST_DIR"),
        "action": os.getenv("PRUNE_DUPLICATES_ACTION", "move"),
        "dry_run": os.getenv("PRUNE_DUPLICATES_DRY_RUN", "false").lower() in ("true", "1", "yes"),
        "min_similarity": float(os.getenv("PRUNE_DUPLICATES_MIN_SIMILARITY", "0.78")),
        "max_days_apart": int(os.getenv("PRUNE_DUPLICATES_MAX_DAYS_APART", "14")),
    }


def build_parser() -> argparse.ArgumentParser:
    """CLI 引数定義を構築する"""
    parser = argparse.ArgumentParser(
        description="複数チャンネル録画の重複を .err の Drop 率で整理する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  uv run prune-duplicates -t "F:\\anime\\test" --dry-run
  uv run prune-duplicates -t "F:\\anime\\test" -d "F:\\anime\\duplicates"
  uv run prune-duplicates -t "F:\\anime\\test" --action delete --dry-run
        """,
    )
    parser.add_argument("-t", "--target-dir", help="対象ディレクトリのパス")
    parser.add_argument("-n", "--dry-run", action="store_true", help="実際には移動や削除をしない")
    parser.add_argument(
        "-d",
        "--destination-dir",
        help="重複ファイルの移動先ディレクトリ。未指定時は対象ディレクトリ配下の _duplicates",
    )
    parser.add_argument(
        "--action",
        choices=("move", "delete"),
        default="move",
        help="重複ファイルに対する処理。デフォルトは move",
    )
    parser.add_argument(
        "--min-similarity",
        type=float,
        default=0.78,
        help="重複候補とみなすシリーズ名類似度の下限。デフォルト 0.78",
    )
    parser.add_argument(
        "--max-days-apart",
        type=int,
        default=14,
        help="同一番組候補とみなす放送日の最大差。デフォルト 14 日",
    )
    return parser


def resolve_action(args: argparse.Namespace, env_config: dict[str, object]) -> str:
    """実行アクションを決定する"""
    if args.action != "move":
        return args.action
    env_action = env_config.get("action")
    if isinstance(env_action, str) and env_action in ("move", "delete"):
        return env_action
    return "move"


def scan_recordings_with_progress(console: Console, target_dir: Path) -> List[RecordingFileSet]:
    """録画ファイルを走査して解析済み一覧を返す"""
    ts_paths = [
        path for path in sorted(target_dir.iterdir(), key=lambda item: item.name)
        if path.is_file() and path.suffix.lower() == ".ts"
    ]

    if not ts_paths:
        return []

    status_text = Text("準備完了", style="cyan")
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    )
    layout = Table.grid(expand=True)
    layout.add_row(status_text)
    layout.add_row(progress)

    recordings: List[RecordingFileSet] = []
    with Live(layout, console=console, refresh_per_second=4):
        task = progress.add_task("録画ファイルをスキャン中...", total=len(ts_paths))
        for ts_path in ts_paths:
            status_text.plain = f"→ {ts_path.name}"
            recordings.append(build_recording_file_set(ts_path))
            progress.advance(task)

    return recordings


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    console = Console()
    env_config = load_config_from_env()

    target_dir_str = args.target_dir or env_config.get("target_dir")
    if not isinstance(target_dir_str, str) or not target_dir_str:
        console.print("[bold red]エラー: 対象ディレクトリを --target-dir または .env で指定してください[/bold red]")
        return 1

    target_dir = Path(target_dir_str).resolve()
    if not target_dir.exists():
        console.print(f"[bold red]エラー: 対象ディレクトリが存在しません: {target_dir}[/bold red]")
        return 1
    if not target_dir.is_dir():
        console.print(f"[bold red]エラー: 対象パスはディレクトリではありません: {target_dir}[/bold red]")
        return 1

    action = resolve_action(args, env_config)
    dry_run = args.dry_run or bool(env_config.get("dry_run", False))

    min_similarity = args.min_similarity
    if args.min_similarity == 0.78 and isinstance(env_config.get("min_similarity"), float):
        min_similarity = env_config["min_similarity"]

    max_days_apart = args.max_days_apart
    if args.max_days_apart == 14 and isinstance(env_config.get("max_days_apart"), int):
        max_days_apart = env_config["max_days_apart"]

    destination_dir_str = args.destination_dir or env_config.get("destination_dir")
    if action == "move":
        if isinstance(destination_dir_str, str) and destination_dir_str:
            destination_dir = Path(destination_dir_str).resolve()
        else:
            destination_dir = (target_dir / "_duplicates").resolve()
    else:
        destination_dir = None

    recordings = scan_recordings_with_progress(console, target_dir)
    if not recordings:
        console.print(Panel(
            "[bold green]対象の TS ファイルは見つかりませんでした[/bold green]",
            title="完了",
            border_style="green",
        ))
        return 0

    console.print("[bold]重複候補を解析中...[/bold]")
    duplicate_groups = find_duplicate_groups(
        recordings,
        min_similarity=min_similarity,
        max_days_apart=max_days_apart,
    )

    if not duplicate_groups:
        console.print(Panel(
            "[bold green]重複候補は見つかりませんでした[/bold green]",
            title="完了",
            border_style="green",
        ))
        return 0

    info_lines = [
        f"[bold]対象ディレクトリ:[/bold] {target_dir}",
        f"[bold]検出TSファイル数:[/bold] {len(recordings)}",
        f"[bold]重複候補グループ数:[/bold] {len(duplicate_groups)}",
        f"[bold]アクション:[/bold] {action}",
        f"[bold]類似度しきい値:[/bold] {min_similarity:.2f}",
        f"[bold]最大日数差:[/bold] {max_days_apart} 日",
    ]
    if destination_dir is not None:
        info_lines.append(f"[bold]移動先:[/bold] {destination_dir}")
    if dry_run:
        info_lines.append("[bold yellow]※ dry-run モード: 実際にはファイルを変更しません[/bold yellow]")
    console.print(Panel("\n".join(info_lines), title="処理概要", border_style="blue"))

    status_text = Text("準備完了", style="cyan")
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    )
    layout = Table.grid(expand=True)
    layout.add_row(status_text)
    layout.add_row(progress)

    results: List[FileResult] = []
    kept_count = 0
    undecided_count = 0

    with Live(layout, console=console, refresh_per_second=4):
        task = progress.add_task("重複候補を処理中...", total=len(duplicate_groups))

        for group_index, group in enumerate(duplicate_groups, start=1):
            group_name = f"G{group_index:03d}"
            title = summarize_group_title(group)
            status_text.plain = f"→ {group_name} {title}"

            best_recording, skip_reason = select_best_recording(group)
            if best_recording is None:
                undecided_count += 1
                for recording in group:
                    results.append(FileResult(
                        group_name=group_name,
                        title=title,
                        service_name=recording.service_name or "-",
                        filename=recording.ts_path.name,
                        kept_filename="-",
                        drop_rate_text=format_drop_rate(recording.err_summary),
                        status=ResultStatus.SKIPPED,
                        message=skip_reason,
                    ))
                progress.advance(task)
                continue

            kept_count += 1
            results.append(FileResult(
                group_name=group_name,
                title=title,
                service_name=best_recording.service_name or "-",
                filename=best_recording.ts_path.name,
                kept_filename=best_recording.ts_path.name,
                drop_rate_text=format_drop_rate(best_recording.err_summary),
                status=ResultStatus.KEPT,
                message="Drop 率が最小のため保持",
            ))

            for recording in group:
                if recording.ts_path == best_recording.ts_path:
                    continue

                status_text.plain = f"→ {group_name} {recording.ts_path.name}"
                drop_rate_text = format_drop_rate(recording.err_summary)
                if dry_run:
                    results.append(FileResult(
                        group_name=group_name,
                        title=title,
                        service_name=recording.service_name or "-",
                        filename=recording.ts_path.name,
                        kept_filename=best_recording.ts_path.name,
                        drop_rate_text=drop_rate_text,
                        status=ResultStatus.DRY_RUN,
                        message=f"{action} 対象",
                    ))
                    continue

                try:
                    if action == "move":
                        assert destination_dir is not None
                        move_recording_safely(recording, destination_dir)
                        status = ResultStatus.MOVED
                        message = f"{destination_dir} へ安全に退避"
                    else:
                        delete_recording_files(recording)
                        status = ResultStatus.DELETED
                        message = "関連ファイルを削除"
                except PermissionError as error:
                    status = ResultStatus.ERROR
                    message = f"ファイルが他のプロセスで使用中の可能性があります: {error}"
                except Exception as error:
                    status = ResultStatus.ERROR
                    message = str(error)

                results.append(FileResult(
                    group_name=group_name,
                    title=title,
                    service_name=recording.service_name or "-",
                    filename=recording.ts_path.name,
                    kept_filename=best_recording.ts_path.name,
                    drop_rate_text=drop_rate_text,
                    status=status,
                    message=message,
                ))

            progress.advance(task)

    table = Table(title="処理詳細", box=box.ROUNDED)
    table.add_column("Group", style="cyan")
    table.add_column("番組", style="green")
    table.add_column("チャンネル", style="yellow")
    table.add_column("ファイル", style="white")
    table.add_column("Drop率", style="magenta")
    table.add_column("結果", style="blue")
    table.add_column("保持対象", style="cyan")

    status_map = {
        ResultStatus.KEPT: "[green]◎ 保持[/green]",
        ResultStatus.MOVED: "[green]✓ 移動完了[/green]",
        ResultStatus.DELETED: "[green]✓ 削除完了[/green]",
        ResultStatus.SKIPPED: "[yellow]⚠ 保留[/yellow]",
        ResultStatus.ERROR: "[red]✗ エラー[/red]",
        ResultStatus.DRY_RUN: "[blue]◎ dry-run[/blue]",
    }
    for result in results:
        table.add_row(
            result.group_name,
            result.title,
            result.service_name,
            result.filename,
            result.drop_rate_text,
            status_map[result.status],
            result.kept_filename,
        )

    console.print()
    console.print(table)

    moved_count = sum(1 for result in results if result.status == ResultStatus.MOVED)
    deleted_count = sum(1 for result in results if result.status == ResultStatus.DELETED)
    error_count = sum(1 for result in results if result.status == ResultStatus.ERROR)
    dry_run_count = sum(1 for result in results if result.status == ResultStatus.DRY_RUN)
    skipped_count = sum(1 for result in results if result.status == ResultStatus.SKIPPED)

    summary_lines = []
    if dry_run:
        summary_lines.append("[bold blue]dry-run 完了[/bold blue]")
        summary_lines.append(f"  [blue]{dry_run_count}[/blue] 件が {action} 対象")
    else:
        summary_lines.append("[bold green]処理完了[/bold green]")
        if moved_count:
            summary_lines.append(f"  [green]{moved_count}[/green] 件を安全に移動")
        if deleted_count:
            summary_lines.append(f"  [green]{deleted_count}[/green] 件を削除")
    summary_lines.append(f"  [green]{kept_count}[/green] グループで保持対象を選定")
    if skipped_count:
        summary_lines.append(f"  [yellow]{skipped_count}[/yellow] 件を保留")
    if undecided_count:
        summary_lines.append(f"  [yellow]{undecided_count}[/yellow] グループは .err 不足などで未確定")
    if error_count:
        summary_lines.append(f"  [red]{error_count}[/red] 件でエラー")

    console.print()
    console.print(Panel("\n".join(summary_lines), title="サマリー", border_style="green"))

    detail_messages = [
        result for result in results if result.message and result.status in (ResultStatus.SKIPPED, ResultStatus.ERROR)
    ]
    if detail_messages:
        detail_table = Table(title="補足", box=box.ROUNDED)
        detail_table.add_column("Group", style="cyan")
        detail_table.add_column("ファイル", style="yellow")
        detail_table.add_column("理由", style="white")
        for result in detail_messages:
            detail_table.add_row(result.group_name, result.filename, result.message)
        console.print()
        console.print(detail_table)

    return 0 if error_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())