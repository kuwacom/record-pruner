#!/usr/bin/env python3
"""
EDCB 出力バグによる壊れた録画ファイル名をメタデータから復旧するスクリプト

ファイル名が Windows 8.3 短いファイル名などに壊れてしまった録画ファイルを、
隣接する .program.txt メタデータから番組情報を読み取り、
Write_Default.so / RecName_Macro.so 互換のマクロパターンで正しいファイル名に復旧する

EDCBのバグにより、TS/TXT/ERRファイルのファイル名が完全に異なってしまう場合がある。
このスクリプトは以下の方法でファイルを関連付ける：
1. TXTファイルの日時情報（録画開始時刻）とTS/ERRファイルのLastWriteTime（録画終了時刻）を照合
2. ServiceIDによる正確なマッチング
"""

import argparse
import os
import re
import shutil
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table

from lib.edcb import (
    BrokenFileInfo,
    build_filename,
    is_broken_filename,
    match_files_by_time_and_service,
    parse_program_txt,
    scan_broken_files,
)
from lib.text import escape_filename


class ResultStatus(Enum):
    """処理結果ステータス"""
    RENAMED = "リネーム完了"
    SKIPPED = "スキップ"
    ERROR = "エラー"
    DRY_RUN = "dry-run"
    NO_METADATA = "メタデータなし"


@dataclass
class FileResult:
    """ファイル処理結果"""
    original_path: Path
    new_name: str
    status: ResultStatus
    message: str = ""


def load_config_from_env() -> dict:
    """
    .env ファイルから設定を読み込む

    @returns 環境変数から読み込んだ設定辞書
    """
    load_dotenv()
    max_filename_length_str = os.getenv("RECOVER_MAX_FILENAME_LENGTH", "10")
    max_filename_length = int(max_filename_length_str) if max_filename_length_str.lower() not in ("none", "", "unlimited") else None
    
    return {
        "target_dir": os.getenv("RECOVER_TARGET_DIR"),
        "extension": os.getenv("RECOVER_EXTENSION", ".ts"),
        "macro_pattern": os.getenv("RECOVER_MACRO_PATTERN", "$SDYYYY$-$SDMM$-$SDDD$_$STHH$-$STMM$_$ServiceName$-$EventName$"),
        "filename_pattern": os.getenv("RECOVER_FILENAME_PATTERN"),
        "escape_with": os.getenv("RECOVER_ESCAPE_WITH", "_"),
        "dry_run": os.getenv("RECOVER_DRY_RUN", "false").lower() in ("true", "1", "yes"),
        "keep_metadata": os.getenv("RECOVER_KEEP_METADATA", "true").lower() in ("true", "1", "yes"),
        "max_filename_length": max_filename_length,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="EDCB 出力バグによる壊れた録画ファイル名をメタデータから復旧する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  uv run recover-filenames -t "F:\\anime\\2025年-夏"
  uv run recover-filenames -t "F:\\anime\\2025年-夏" --dry-run
  uv run recover-filenames -t "F:\\anime\\2025年-夏" --macro-pattern "$SDYYYY$$SDMM$$SDDD$_$STHH$$STMM$_$EventName$"
        """,
    )
    parser.add_argument(
        "-t", "--target-dir",
        help="対象ディレクトリのパス",
    )
    parser.add_argument(
        "-n", "--dry-run",
        action="store_true",
        help="実際にリネームせずシミュレーションのみ実行する",
    )
    parser.add_argument(
        "--extension",
        default=".ts",
        help="対象ファイルの拡張子（デフォルト: .ts）",
    )
    parser.add_argument(
        "--macro-pattern",
        default="$SDYYYY$-$SDMM$-$SDDD$_$STHH$-$STMM$_$ServiceName$-$EventName$",
        help="RecName_Macro.so 互換のマクロパターン",
    )
    parser.add_argument(
        "--filename-pattern",
        help="壊れたファイル名を検出するカスタム正規表現パターン（未指定時は ~ を含むファイルを対象）",
    )
    parser.add_argument(
        "--escape-with",
        default="_",
        help="Windows 禁止文字のエスケープ文字（デフォルト: _）",
    )
    parser.add_argument(
        "--keep-metadata",
        action="store_true",
        default=True,
        help="メタデータファイルも新しいファイル名に付け替える（デフォルト: true）",
    )
    parser.add_argument(
        "--no-keep-metadata",
        action="store_true",
        help="メタデータファイルは元のファイル名のままにする",
    )
    parser.add_argument(
        "--max-filename-length",
        type=int,
        default=10,
        help="入力ファイル名の最大長さ（拡張子除く）。デフォルト10文字。0または負の値で無制限",
    )

    args = parser.parse_args()
    console = Console()

    # .env から設定を読み込み、引数が優先
    env_config = load_config_from_env()

    target_dir_str = args.target_dir or env_config.get("target_dir")
    if not target_dir_str:
        console.print("[bold red]エラー: 対象ディレクトリを --target-dir または .env の RECOVER_TARGET_DIR で指定してください[/bold red]")
        return 1

    target_dir = Path(target_dir_str).resolve()
    if not target_dir.exists():
        console.print(f"[bold red]エラー: 対象ディレクトリが存在しません: {target_dir}[/bold red]")
        return 1
    if not target_dir.is_dir():
        console.print(f"[bold red]エラー: 対象パスはディレクトリではありません: {target_dir}[/bold red]")
        return 1

    # 引数 > .env の優先順位で設定を決定
    macro_pattern = args.macro_pattern if args.macro_pattern != "$SDYYYY$-$SDMM$-$SDDD$_$STHH$-$STMM$_$ServiceName$-$EventName$" or not env_config.get("macro_pattern") else env_config["macro_pattern"]
    if args.macro_pattern != "$SDYYYY$-$SDMM$-$SDDD$_$STHH$-$STMM$_$ServiceName$-$EventName$":
        macro_pattern = args.macro_pattern
    elif env_config.get("macro_pattern"):
        macro_pattern = env_config["macro_pattern"]

    filename_pattern = args.filename_pattern or env_config.get("filename_pattern")

    escape_with = args.escape_with if args.escape_with != "_" or not env_config.get("escape_with") else env_config["escape_with"]
    if args.escape_with != "_":
        escape_with = args.escape_with
    elif env_config.get("escape_with"):
        escape_with = env_config["escape_with"]

    dry_run = args.dry_run or env_config.get("dry_run", False)

    keep_metadata = True
    if args.no_keep_metadata:
        keep_metadata = False
    elif env_config.get("keep_metadata") is not None:
        keep_metadata = env_config["keep_metadata"]

    # max_filename_lengthの設定（引数 > .env）
    max_filename_length = args.max_filename_length
    if max_filename_length <= 0:
        max_filename_length = None
    elif env_config.get("max_filename_length") is not None and args.max_filename_length == 10:
        max_filename_length = env_config["max_filename_length"]

    # 壊れたファイルをスキャン
    console.print("[bold]壊れたファイルをスキャン中...[/bold]")
    ts_files, txt_files, err_files = scan_broken_files(target_dir, max_filename_length)

    if not ts_files:
        console.print(Panel(
            f"[bold green]壊れたTSファイルは見つかりませんでした[/bold green]",
            title="完了",
            border_style="green",
        ))
        return 0

    # ファイルをマッチング
    console.print(f"[bold]TS: {len(ts_files)}, TXT: {len(txt_files)}, ERR: {len(err_files)} ファイルを検出[/bold]")
    console.print("[bold]ファイルの関連付けを解析中...[/bold]")
    matched_files = match_files_by_time_and_service(ts_files, txt_files, err_files)

    # 情報パネル表示
    max_len_str = str(max_filename_length) if max_filename_length is not None else "無制限"
    info_lines = [
        f"[bold]対象ディレクトリ:[/bold] {target_dir}",
        f"[bold]マクロパターン:[/bold] {macro_pattern}",
        f"[bold]ファイル名最大長さ:[/bold] {max_len_str}",
        f"[bold]検出TSファイル数:[/bold] {len(ts_files)}",
        f"[bold]検出TXTファイル数:[/bold] {len(txt_files)}",
        f"[bold]検出ERRファイル数:[/bold] {len(err_files)}",
    ]
    if dry_run:
        info_lines.append("[bold yellow]※ dry-run モード: 実際にはリネームしません[/bold yellow]")
    console.print(Panel("\n".join(info_lines), title="処理概要", border_style="blue"))

    results: List[FileResult] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("ファイル処理中...", total=len(ts_files))

        for ts_info in ts_files:
            txt_info, err_info = matched_files.get(ts_info.path, (None, None))
            
            if txt_info is None or txt_info.program_info is None:
                results.append(FileResult(
                    original_path=ts_info.path,
                    new_name="",
                    status=ResultStatus.NO_METADATA,
                    message="対応するメタデータファイルが見つかりません",
                ))
                progress.advance(task)
                continue

            program_info = txt_info.program_info

            # 新しいファイル名を生成
            new_name_raw = build_filename(macro_pattern, program_info)
            new_name_escaped = escape_filename(new_name_raw, escape_with)
            
            # マクロパターンに拡張子が含まれているかチェック
            # .ts で終わる場合は追加の拡張子を付けない
            if new_name_escaped.lower().endswith(".ts"):
                new_name = new_name_escaped
            else:
                new_name = new_name_escaped + ".ts"
            
            dest_path = target_dir / new_name

            if dry_run:
                results.append(FileResult(
                    original_path=ts_info.path,
                    new_name=new_name,
                    status=ResultStatus.DRY_RUN,
                ))
                progress.advance(task)
                continue

            # 移動先に同名ファイルが存在する場合はスキップ
            if dest_path.exists():
                results.append(FileResult(
                    original_path=ts_info.path,
                    new_name=new_name,
                    status=ResultStatus.SKIPPED,
                    message=f"同名ファイルが存在: {new_name}",
                ))
                progress.advance(task)
                continue

            try:
                shutil.move(str(ts_info.path), str(dest_path))

                # TXTファイルも付け替える
                if keep_metadata and txt_info and txt_info.path.exists():
                    new_txt_name = new_name + ".program.txt"
                    new_txt_path = target_dir / new_txt_name
                    if not new_txt_path.exists():
                        shutil.move(str(txt_info.path), str(new_txt_path))

                # ERRファイルも付け替える
                if keep_metadata and err_info and err_info.path.exists():
                    new_err_name = new_name + ".err"
                    new_err_path = target_dir / new_err_name
                    if not new_err_path.exists():
                        shutil.move(str(err_info.path), str(new_err_path))

                results.append(FileResult(
                    original_path=ts_info.path,
                    new_name=new_name,
                    status=ResultStatus.RENAMED,
                ))
            except PermissionError as e:
                error_msg = (
                    f"ファイルが他のプロセスで使用中の可能性があります: {e}\n"
                    "録画ソフトやメディアサーバー（Plex / Jellyfin 等）を停止してから再実行してください"
                )
                console.print(f"[bold red]  {error_msg}[/bold red]")
                results.append(FileResult(
                    original_path=ts_info.path,
                    new_name=new_name,
                    status=ResultStatus.ERROR,
                    message=error_msg,
                ))
            except Exception as e:
                console.print(f"[bold red]  エラー: {e}[/bold red]")
                results.append(FileResult(
                    original_path=ts_info.path,
                    new_name=new_name,
                    status=ResultStatus.ERROR,
                    message=str(e),
                ))
            progress.advance(task)

    # 結果テーブル表示
    if results:
        table = Table(title="処理詳細", box=box.ROUNDED)
        table.add_column("元ファイル名", style="cyan")
        table.add_column("新ファイル名", style="green")
        table.add_column("結果", style="yellow")

        for r in results:
            status_style = {
                ResultStatus.RENAMED: "[green]✓ リネーム完了[/green]",
                ResultStatus.SKIPPED: "[yellow]⚠ スキップ[/yellow]",
                ResultStatus.ERROR: "[red]✗ エラー[/red]",
                ResultStatus.DRY_RUN: "[blue]◎ dry-run[/blue]",
                ResultStatus.NO_METADATA: "[dim]⊘ メタデータなし[/dim]",
            }[r.status]
            table.add_row(r.original_path.name, r.new_name, status_style)

        console.print()
        console.print(table)

    # サマリー表示
    renamed_count = sum(1 for r in results if r.status == ResultStatus.RENAMED)
    skipped_count = sum(1 for r in results if r.status == ResultStatus.SKIPPED)
    error_count = sum(1 for r in results if r.status == ResultStatus.ERROR)
    dry_run_count = sum(1 for r in results if r.status == ResultStatus.DRY_RUN)
    no_metadata_count = sum(1 for r in results if r.status == ResultStatus.NO_METADATA)

    summary_lines = []
    if dry_run:
        summary_lines.append("[bold blue]dry-run 完了[/bold blue]")
        summary_lines.append(f"  {dry_run_count} ファイルがリネーム対象です")
    else:
        summary_lines.append("[bold green]処理完了[/bold green]")
        summary_lines.append(f"  [green]{renamed_count}[/green] ファイルをリネーム")
        if skipped_count:
            summary_lines.append(f"  [yellow]{skipped_count}[/yellow] ファイルをスキップ")
        if error_count:
            summary_lines.append(f"  [red]{error_count}[/red] エラー")
        if no_metadata_count:
            summary_lines.append(f"  [dim]{no_metadata_count}[/dim] メタデータなし")

    console.print()
    console.print(Panel("\n".join(summary_lines), title="サマリー", border_style="green"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
