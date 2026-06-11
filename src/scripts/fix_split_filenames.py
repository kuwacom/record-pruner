#!/usr/bin/env python3
"""
録画ファイルのフォルダ誤分割修正スクリプト

ファイル名に含まれる全角括弧などの影響で、録画ソフトがファイル名を途中で
フォルダとファイルに分割してしまったケースを修正する
フォルダ名とファイル名を結合して元のファイル名を復元し、元のディレクトリに移動する
"""

import argparse
import shutil
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Tuple

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table

from lib.text import escape_filename


class ResultStatus(Enum):
    """処理結果ステータス"""
    MOVED = "移動完了"
    SKIPPED = "スキップ"
    ERROR = "エラー"
    DRY_RUN = "dry-run"


@dataclass
class FileResult:
    """ファイル処理結果"""
    folder_name: str
    original_name: str
    new_name: str
    status: ResultStatus
    message: str = ""


def find_split_folders(target_dir: Path) -> List[Tuple[Path, List[Path]]]:
    """
    対象ディレクトリ内の分割フォルダを検出する

    直下のフォルダで、内部にファイルが1つ以上存在するものを対象とする

    @param target_dir - 走査対象のディレクトリ
    @returns (フォルダパス, フォルダ内ファイルリスト) のタプルリスト
    """
    result: List[Tuple[Path, List[Path]]] = []
    for entry in target_dir.iterdir():
        if not entry.is_dir():
            continue
        # ファイルのみを収集（サブディレクトリは無視）
        files = [f for f in entry.iterdir() if f.is_file()]
        if files:
            result.append((entry, files))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="録画ファイルのフォルダ誤分割を修正する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  uv run fix-split-filenames -t "F:\\anime\\2025年-夏"
  uv run fix-split-filenames -t "F:\\anime\\2025年-夏" --dry-run
  uv run fix-split-filenames -t "F:\\anime\\2025年-夏" --escape-with "_"
        """,
    )
    parser.add_argument(
        "-t", "--target-dir",
        required=True,
        help="対象ディレクトリのパス",
    )
    parser.add_argument(
        "-n", "--dry-run",
        action="store_true",
        help="実際に移動せずシミュレーションのみ実行する",
    )
    parser.add_argument(
        "--escape-with",
        default="_",
        help="Windows 禁止文字のエスケープ文字（デフォルト: _）",
    )
    parser.add_argument(
        "--keep-empty",
        action="store_true",
        help="空になったフォルダを削除しない",
    )
    parser.add_argument(
        "--join-with",
        default="",
        help="フォルダ名とファイル名の間に挿入する文字列（デフォルト: 空文字）",
    )

    args = parser.parse_args()
    console = Console()

    target_dir = Path(args.target_dir).resolve()
    if not target_dir.exists():
        console.print(f"[bold red]エラー: 対象ディレクトリが存在しません: {target_dir}[/bold red]")
        return 1
    if not target_dir.is_dir():
        console.print(f"[bold red]エラー: 対象パスはディレクトリではありません: {target_dir}[/bold red]")
        return 1

    split_folders = find_split_folders(target_dir)

    if not split_folders:
        console.print(Panel(
            "[bold green]修正が必要な分割フォルダは見つかりませんでした[/bold green]",
            title="完了",
            border_style="green",
        ))
        return 0

    # ファイル総数を計算
    total_files = sum(len(files) for _, files in split_folders)

    # 情報パネル表示
    info_lines = [
        f"[bold]対象ディレクトリ:[/bold] {target_dir}",
        f"[bold]分割フォルダ数:[/bold] {len(split_folders)}",
        f"[bold]対象ファイル数:[/bold] {total_files}",
    ]
    if args.dry_run:
        info_lines.append("[bold yellow]※ dry-run モード: 実際には移動しません[/bold yellow]")
    console.print(Panel("\n".join(info_lines), title="処理概要", border_style="blue"))

    results: List[FileResult] = []
    removed_folders = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("ファイル処理中...", total=total_files)

        for folder, files in split_folders:
            folder_name = folder.name

            for file in files:
                # 結合後のファイル名 = フォルダ名 + join_with + ファイル名
                combined_name = folder_name + args.join_with + file.name
                escaped_name = escape_filename(combined_name, args.escape_with)
                dest_path = target_dir / escaped_name

                if args.dry_run:
                    results.append(FileResult(
                        folder_name=folder_name,
                        original_name=file.name,
                        new_name=escaped_name,
                        status=ResultStatus.DRY_RUN,
                    ))
                    progress.advance(task)
                    continue

                # 移動先に同名ファイルが存在する場合はスキップ
                if dest_path.exists():
                    results.append(FileResult(
                        folder_name=folder_name,
                        original_name=file.name,
                        new_name=escaped_name,
                        status=ResultStatus.SKIPPED,
                        message=f"同名ファイルが存在: {dest_path.name}",
                    ))
                    progress.advance(task)
                    continue

                try:
                    shutil.move(str(file), str(dest_path))
                    results.append(FileResult(
                        folder_name=folder_name,
                        original_name=file.name,
                        new_name=escaped_name,
                        status=ResultStatus.MOVED,
                    ))
                except PermissionError as e:
                    error_msg = (
                        f"ファイルが他のプロセスで使用中の可能性があります: {e}\n"
                        "録画ソフトやメディアサーバー（Plex / Jellyfin 等）を停止してから再実行してください"
                    )
                    console.print(f"[bold red]  {error_msg}[/bold red]")
                    results.append(FileResult(
                        folder_name=folder_name,
                        original_name=file.name,
                        new_name=escaped_name,
                        status=ResultStatus.ERROR,
                        message=error_msg,
                    ))
                except Exception as e:
                    console.print(f"[bold red]  エラー: {e}[/bold red]")
                    results.append(FileResult(
                        folder_name=folder_name,
                        original_name=file.name,
                        new_name=escaped_name,
                        status=ResultStatus.ERROR,
                        message=str(e),
                    ))
                progress.advance(task)

            # ファイル移動後、フォルダが空になっていれば削除
            if not args.keep_empty and not args.dry_run:
                remaining = list(folder.iterdir())
                if not remaining:
                    try:
                        folder.rmdir()
                        removed_folders += 1
                    except Exception:
                        pass  # 削除失敗は無視

    # 結果テーブル表示
    if results:
        table = Table(title="処理詳細", box=box.ROUNDED)
        table.add_column("フォルダ", style="cyan")
        table.add_column("元ファイル", style="magenta")
        table.add_column("結合後ファイル名", style="green")
        table.add_column("結果", style="yellow")

        for r in results:
            status_style = {
                ResultStatus.MOVED: "[green]✓ 移動完了[/green]",
                ResultStatus.SKIPPED: "[yellow]⚠ スキップ[/yellow]",
                ResultStatus.ERROR: "[red]✗ エラー[/red]",
                ResultStatus.DRY_RUN: "[blue]◎ dry-run[/blue]",
            }[r.status]
            table.add_row(r.folder_name, r.original_name, r.new_name, status_style)

        console.print()
        console.print(table)

    # サマリー表示
    moved_count = sum(1 for r in results if r.status == ResultStatus.MOVED)
    skipped_count = sum(1 for r in results if r.status == ResultStatus.SKIPPED)
    error_count = sum(1 for r in results if r.status == ResultStatus.ERROR)
    dry_run_count = sum(1 for r in results if r.status == ResultStatus.DRY_RUN)

    summary_lines = []
    if args.dry_run:
        summary_lines.append(f"[bold blue]dry-run 完了[/bold blue]")
        summary_lines.append(f"  {dry_run_count} ファイルが移動対象です")
    else:
        summary_lines.append(f"[bold green]処理完了[/bold green]")
        summary_lines.append(f"  [green]{moved_count}[/green] ファイルを移動")
        if skipped_count:
            summary_lines.append(f"  [yellow]{skipped_count}[/yellow] ファイルをスキップ")
        if error_count:
            summary_lines.append(f"  [red]{error_count}[/red] エラー")
        if removed_folders:
            summary_lines.append(f"  [green]{removed_folders}[/green] 空フォルダを削除")

    console.print()
    console.print(Panel("\n".join(summary_lines), title="サマリー", border_style="green"))

    return 0


if __name__ == "__main__":
    sys.exit(main())

