#!/usr/bin/env python3
"""
既存ファイル群のエンコーディングを統一するスクリプト

指定したパターンにマッチするファイルを検索し、自動検出したエンコーディングが
目標エンコーディングと異なる場合は変換して書き戻す
"""

import argparse
import fnmatch
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional

from rich import box
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table
from rich.text import Text

from lib.file_io import detect_encoding, read_text_file_auto, write_text_file_preserve_timestamp


class ResultStatus(Enum):
    """処理結果ステータス"""
    CONVERTED = "変換完了"
    ALREADY_OK = "既に正しい"
    SKIPPED = "スキップ"
    ERROR = "エラー"
    DRY_RUN = "dry-run"


@dataclass
class FileResult:
    """ファイル処理結果"""
    path: Path
    original_encoding: str
    target_encoding: str
    status: ResultStatus
    message: str = ""


def find_matching_files(target_dir: Path, pattern: str, recursive: bool) -> List[Path]:
    """
    指定したパターンにマッチするファイルを検索する

    @param target_dir - 検索対象ディレクトリ
    @param pattern - ファイルパターン（例: *.txt, *.program.txt）
    @param recursive - サブディレクトリも検索するか
    @returns マッチしたファイルパスのリスト
    """
    results: List[Path] = []
    if recursive:
        for file_path in target_dir.rglob("*"):
            if file_path.is_file() and fnmatch.fnmatch(file_path.name, pattern):
                results.append(file_path)
    else:
        for file_path in target_dir.iterdir():
            if file_path.is_file() and fnmatch.fnmatch(file_path.name, pattern):
                results.append(file_path)
    return sorted(results)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ファイル群のエンコーディングを統一する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  uv run convert-encoding -t "F:\\anime\\2025年-夏" --pattern "*.program.txt"
  uv run convert-encoding -t "F:\\anime\\2025年-夏" --pattern "*.txt" --to-encoding utf-8 --recursive
  uv run convert-encoding -t "F:\\anime\\2025年-夏" --pattern "*.txt" --dry-run
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
        help="実際に変換せずシミュレーションのみ実行する",
    )
    parser.add_argument(
        "--pattern",
        default="*.txt",
        help="対象ファイルのパターン（デフォルト: *.txt）",
    )
    parser.add_argument(
        "--to-encoding",
        default="utf-8-sig",
        help="目標エンコーディング（デフォルト: utf-8-sig = BOM付きUTF-8）",
    )
    parser.add_argument(
        "-r", "--recursive",
        action="store_true",
        help="サブディレクトリも検索する",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="エンコーディングが同じでも強制的に書き換える",
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

    # 対象ファイルを検索
    console.print(f"[bold]パターン '{args.pattern}' にマッチするファイルを検索中...[/bold]")
    files = find_matching_files(target_dir, args.pattern, args.recursive)

    if not files:
        console.print(Panel(
            f"[bold green]パターンにマッチするファイルは見つかりませんでした[/bold green]",
            title="完了",
            border_style="green",
        ))
        return 0

    # 情報パネル表示
    info_lines = [
        f"[bold]対象ディレクトリ:[/bold] {target_dir}",
        f"[bold]ファイルパターン:[/bold] {args.pattern}",
        f"[bold]目標エンコーディング:[/bold] {args.to_encoding}",
        f"[bold]検出ファイル数:[/bold] {len(files)}",
    ]
    if args.recursive:
        info_lines.append("[bold]サブディレクトリ:[/bold] 含む")
    if args.dry_run:
        info_lines.append("[bold yellow]※ dry-run モード: 実際には変換しません[/bold yellow]")
    console.print(Panel("\n".join(info_lines), title="処理概要", border_style="blue"))

    results: List[FileResult] = []

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

    with Live(layout, console=console, refresh_per_second=4):
        task = progress.add_task("ファイル処理中...", total=len(files))

        for file_path in files:
            status_text.plain = f"→ {file_path.name}"

            try:
                # エンコーディングを自動検出
                original_encoding = detect_encoding(file_path)

                # 目標エンコーディングと同じ場合はスキップ
                if original_encoding == args.to_encoding and not args.force:
                    results.append(FileResult(
                        path=file_path,
                        original_encoding=original_encoding,
                        target_encoding=args.to_encoding,
                        status=ResultStatus.ALREADY_OK,
                    ))
                    progress.advance(task)
                    continue

                # ファイル内容を読み込む
                content, _ = read_text_file_auto(file_path)

                if args.dry_run:
                    results.append(FileResult(
                        path=file_path,
                        original_encoding=original_encoding,
                        target_encoding=args.to_encoding,
                        status=ResultStatus.DRY_RUN,
                    ))
                    progress.advance(task)
                    continue

                # 目標エンコーディングで書き戻す
                write_text_file_preserve_timestamp(file_path, content, encoding=args.to_encoding)

                results.append(FileResult(
                    path=file_path,
                    original_encoding=original_encoding,
                    target_encoding=args.to_encoding,
                    status=ResultStatus.CONVERTED,
                ))

            except Exception as e:
                console.print(f"[bold red]  エラー: {e}[/bold red]")
                results.append(FileResult(
                    path=file_path,
                    original_encoding="unknown",
                    target_encoding=args.to_encoding,
                    status=ResultStatus.ERROR,
                    message=str(e),
                ))
            progress.advance(task)

    # 結果テーブル表示
    if results:
        table = Table(title="処理詳細", box=box.ROUNDED)
        table.add_column("ファイル名", style="cyan")
        table.add_column("元エンコ", style="yellow")
        table.add_column("目標エンコ", style="green")
        table.add_column("結果", style="magenta")

        for r in results:
            status_style = {
                ResultStatus.CONVERTED: "[green]✓ 変換完了[/green]",
                ResultStatus.ALREADY_OK: "[dim]⇄ 既に正しい[/dim]",
                ResultStatus.SKIPPED: "[yellow]⚠ スキップ[/yellow]",
                ResultStatus.ERROR: "[red]✗ エラー[/red]",
                ResultStatus.DRY_RUN: "[blue]◎ dry-run[/blue]",
            }[r.status]
            table.add_row(
                str(r.path.relative_to(target_dir)) if r.path.is_relative_to(target_dir) else r.path.name,
                r.original_encoding,
                r.target_encoding,
                status_style,
            )

        console.print()
        console.print(table)

    # サマリー表示
    converted_count = sum(1 for r in results if r.status == ResultStatus.CONVERTED)
    already_ok_count = sum(1 for r in results if r.status == ResultStatus.ALREADY_OK)
    error_count = sum(1 for r in results if r.status == ResultStatus.ERROR)
    dry_run_count = sum(1 for r in results if r.status == ResultStatus.DRY_RUN)

    summary_lines = []
    if args.dry_run:
        summary_lines.append("[bold blue]dry-run 完了[/bold blue]")
        summary_lines.append(f"  {dry_run_count} ファイルが変換対象です")
        summary_lines.append(f"  {already_ok_count} ファイルは既に正しいエンコーディングです")
    else:
        summary_lines.append("[bold green]処理完了[/bold green]")
        summary_lines.append(f"  [green]{converted_count}[/green] ファイルを変換")
        if already_ok_count:
            summary_lines.append(f"  [dim]{already_ok_count}[/dim] 既に正しいエンコーディング")
        if error_count:
            summary_lines.append(f"  [red]{error_count}[/red] エラー")

    console.print()
    console.print(Panel("\n".join(summary_lines), title="サマリー", border_style="green"))

    return 0


if __name__ == "__main__":
    sys.exit(main())

