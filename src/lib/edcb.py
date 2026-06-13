#!/usr/bin/env python3
"""
EDCB 録画メタデータ解析・マクロ置換ライブラリ

RecName_Macro.dll 互換のマクロ変数をサポート
マクロ形式: $MACRO$ または $Function(Args)$
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Tuple, Dict


@dataclass
class ProgramInfo:
    start_time: datetime
    end_time: datetime
    service_name: str
    title: str
    title2: str = ""
    subtitle: str = ""
    sid: Optional[int] = None


@dataclass
class BrokenFileInfo:
    """バグで壊れたファイルの情報"""
    path: Path
    file_type: str  # "TS", "TXT", "ERR"
    last_write_time: datetime
    service_id: Optional[int] = None
    program_info: Optional[ProgramInfo] = None


_DATETIME_PATTERN = re.compile(
    r"^(\d{4})年(\d{1,2})月(\d{1,2})日\(([^)]+)\)\s+(\d{1,2})時(\d{1,2})分"
)
_DATETIME_END_SAME_DAY_PATTERN = re.compile(
    r"^(\d{4})年(\d{1,2})月(\d{1,2})日\(([^)]+)\)\s+(\d{1,2})時(\d{1,2})分～(\d{1,2})時(\d{1,2})分"
)

# EDCB TXT形式の日時パターン（例：2025/07/11(金) 23:30～00:00）
_EDCB_DATETIME_PATTERN = re.compile(
    r"^(\d{4})/(\d{2})/(\d{2})\(([^)]+)\)\s+(\d{2}):(\d{2})～(\d{2}):(\d{2})"
)

_WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]
_TAG_PATTERN = re.compile(r"\[[^\]]+\]")


def _to_halfwidth(text: str) -> str:
    """
    全角英数字・記号のみを半角に変換する
    カタカナ・ひらがな・漢字はそのまま
    """
    result = ""
    for ch in text:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            # 全角ASCII範囲（英数字・記号）を半角に
            result += chr(code - 0xFEE0)
        elif code == 0x3000:
            # 全角スペースを半角スペースに
            result += " "
        else:
            result += ch
    return result


def _remove_tags(text: str) -> str:
    return _TAG_PATTERN.sub("", text).strip()


def _weekday_jp(dt: datetime) -> str:
    return _WEEKDAYS[dt.weekday()]


def _weekday_num(dt: datetime) -> str:
    return str(dt.weekday())


def parse_edcb_datetime(line: str) -> Tuple[datetime, datetime]:
    """
    EDCB TXT形式の日時行を解析する
    例: 2025/07/11(金) 23:30～00:00
    
    @param line - 日時行
    @returns (開始時刻, 終了時刻) のタプル
    @raises ValueError - 解析失敗時
    """
    match = _EDCB_DATETIME_PATTERN.match(line)
    if not match:
        raise ValueError(f"日時解析失敗: {line}")
    
    year = int(match.group(1))
    month = int(match.group(2))
    day = int(match.group(3))
    start_hour = int(match.group(5))
    start_minute = int(match.group(6))
    end_hour = int(match.group(7))
    end_minute = int(match.group(8))
    
    start_time = datetime(year, month, day, start_hour, start_minute, 0)
    
    # 終了時刻が開始時刻より前の場合は翌日
    if end_hour < start_hour or (end_hour == start_hour and end_minute < start_minute):
        end_time = start_time + timedelta(days=1)
        end_time = end_time.replace(hour=end_hour, minute=end_minute)
    else:
        end_time = start_time.replace(hour=end_hour, minute=end_minute)
    
    return start_time, end_time


def parse_service_id(content: str) -> Optional[int]:
    """
    TXTファイルの内容からServiceIDを抽出する
    
    @param content - TXTファイルの内容
    @returns ServiceID（見つからなければNone）
    """
    match = re.search(r"ServiceID:(\d+)", content)
    if match:
        return int(match.group(1))
    return None


def parse_err_service_id(content: str) -> Optional[int]:
    """
    ERRファイルの内容からServiceIDを抽出する
    PMT(ServiceID 0x5C38) 形式から抽出
    
    @param content - ERRファイルの内容
    @returns ServiceID（見つからなければNone）
    """
    match = re.search(r"PMT\\(ServiceID\\s+0x([0-9A-Fa-f]+)\\)", content)
    if match:
        return int(match.group(1), 16)
    return None


def parse_program_txt(content: str) -> Optional[ProgramInfo]:
    # BOMを除去
    content = content.lstrip("\ufeff")
    
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if len(lines) < 3:
        return None

    # EDCB TXT形式の日時を試す
    try:
        start_time, end_time = parse_edcb_datetime(lines[0])
    except ValueError:
        # 従来の形式を試す
        dt_match = _DATETIME_END_SAME_DAY_PATTERN.match(lines[0])
        if dt_match:
            year = int(dt_match.group(1))
            month = int(dt_match.group(2))
            day = int(dt_match.group(3))
            start_hour = int(dt_match.group(5))
            start_minute = int(dt_match.group(6))
            end_hour = int(dt_match.group(7))
            end_minute = int(dt_match.group(8))
            try:
                start_time = datetime(year, month, day, start_hour, start_minute, 0)
            except ValueError:
                return None
            if end_hour < start_hour or (end_hour == start_hour and end_minute < start_minute):
                end_time = start_time + timedelta(days=1)
                end_time = end_time.replace(hour=end_hour, minute=end_minute)
            else:
                end_time = start_time.replace(hour=end_hour, minute=end_minute)
        else:
            dt_match = _DATETIME_PATTERN.match(lines[0])
            if not dt_match:
                return None
            year = int(dt_match.group(1))
            month = int(dt_match.group(2))
            day = int(dt_match.group(3))
            hour = int(dt_match.group(5))
            minute = int(dt_match.group(6))
            try:
                start_time = datetime(year, month, day, hour, minute, 0)
            except ValueError:
                return None
            end_time = start_time

    service_name = lines[1]
    title = lines[2]
    title2 = _remove_tags(title)
    subtitle = lines[3] if len(lines) > 3 else ""
    
    # ServiceIDを抽出
    sid = parse_service_id(content)

    return ProgramInfo(
        start_time=start_time,
        end_time=end_time,
        service_name=service_name,
        title=title,
        title2=title2,
        subtitle=subtitle,
        sid=sid,
    )


def build_filename(pattern: str, info: ProgramInfo) -> str:
    sdt = info.start_time
    edt = info.end_time

    replacements = {
        "Title": info.title,
        "Title2": info.title2,
        "EventName": info.title,
        "EventName2": info.title2,
        "SubTitle": info.subtitle,
        "ServiceName": info.service_name,
        "SDYYYY": f"{sdt.year:04d}",
        "SDYY": f"{sdt.year % 100:02d}",
        "SDMM": f"{sdt.month:02d}",
        "SDM": str(sdt.month),
        "SDDD": f"{sdt.day:02d}",
        "SDD": str(sdt.day),
        "SDW": _weekday_num(sdt),
        "SDw": _weekday_jp(sdt),
        "STHH": f"{sdt.hour:02d}",
        "STH": str(sdt.hour),
        "STMM": f"{sdt.minute:02d}",
        "STM": str(sdt.minute),
        "STSS": f"{sdt.second:02d}",
        "STS": str(sdt.second),
        "EDYYYY": f"{edt.year:04d}",
        "EDYY": f"{edt.year % 100:02d}",
        "EDMM": f"{edt.month:02d}",
        "EDM": str(edt.month),
        "EDDD": f"{edt.day:02d}",
        "EDD": str(edt.day),
        "EDW": _weekday_num(edt),
        "EDw": _weekday_jp(edt),
        "ETHH": f"{edt.hour:02d}",
        "ETH": str(edt.hour),
        "ETMM": f"{edt.minute:02d}",
        "ETM": str(edt.minute),
        "ETSS": f"{edt.second:02d}",
        "ETS": str(edt.second),
        "SID10": str(info.sid) if info.sid is not None else "",
        "SID16": f"{info.sid:X}" if info.sid is not None else "",
    }

    def evaluate_arg(arg: str) -> str:
        arg = arg.strip()
        if arg in replacements:
            return replacements[arg]
        ztoh_match = re.match(r"^ZtoH\((.+)\)$", arg)
        if ztoh_match:
            inner = ztoh_match.group(1)
            inner_val = evaluate_arg(inner)
            return _to_halfwidth(inner_val)
        htoz_match = re.match(r"^HtoZ\((.+)\)$", arg)
        if htoz_match:
            inner = htoz_match.group(1)
            inner_val = evaluate_arg(inner)
            return inner_val
        head_match = re.match(r"^Head(\d+)\((.+)\)$", arg)
        if head_match:
            n = int(head_match.group(1))
            inner = head_match.group(2)
            inner_val = evaluate_arg(inner)
            return inner_val[:n]
        return arg


    def find_function_end(text: str, start: int) -> int:
        depth = 0
        i = start
        while i < len(text):
            if text[i] == "(":
                depth += 1
            elif text[i] == ')':
                depth -= 1
                if depth == 0:
                    if i + 1 < len(text) and text[i + 1] == '$':
                        return i + 1
                    return -1
            i += 1
        return -1

    def _apply_tr(text: str, pairs: list[tuple[str, str]]) -> str:
        for search, replace in pairs:
            text = text.replace(search, replace)
        return text

    def _apply_s(text: str, pairs: list[tuple[str, str]]) -> str:
        for search, replace in pairs:
            text = re.sub(search, replace, text)
        return text

    result = pattern
    max_iterations = 20
    for _ in range(max_iterations):
        # Trマクロ: $Tr/search/replace/(arg)$
        tr_idx = result.find("$Tr/")
        if tr_idx != -1:
            arg_paren = result.find("(", tr_idx + 4)
            if arg_paren != -1:
                spec = result[tr_idx + 4:arg_paren]
                parts = [p for p in spec.split("/") if p]
                if len(parts) >= 2 and len(parts) % 2 == 0:
                    pairs = [(parts[i], parts[i + 1]) for i in range(0, len(parts), 2)]
                    func_end = find_function_end(result, arg_paren)
                    if func_end != -1:
                        arg_val = evaluate_arg(result[arg_paren + 1:func_end - 1])
                        replaced = _apply_tr(arg_val, pairs)
                        result = result[:tr_idx] + replaced + result[func_end + 1:]
                        continue

        # Sマクロ: $S/regex/replace/(arg)$
        s_idx = result.find("$S/")
        if s_idx != -1:
            arg_paren = result.find("(", s_idx + 3)
            if arg_paren != -1:
                spec = result[s_idx + 3:arg_paren]
                parts = [p for p in spec.split("/") if p]
                if len(parts) >= 2 and len(parts) % 2 == 0:
                    pairs = [(parts[i], parts[i + 1]) for i in range(0, len(parts), 2)]
                    func_end = find_function_end(result, arg_paren)
                    if func_end != -1:
                        arg_val = evaluate_arg(result[arg_paren + 1:func_end - 1])
                        replaced = _apply_s(arg_val, pairs)
                        result = result[:s_idx] + replaced + result[func_end + 1:]
                        continue

        func_start = re.search(r"\$(ZtoH|HtoZ|Head\d+)\(", result)
        if not func_start:
            break
        func_name = func_start.group(1)
        paren_start = func_start.end() - 1
        func_end = find_function_end(result, paren_start)
        if func_end == -1:
            break
        func_arg = result[paren_start + 1:func_end - 1]
        evaluated = evaluate_arg(func_arg)
        if func_name == "ZtoH":
            evaluated = _to_halfwidth(evaluated)
        elif func_name == "HtoZ":
            pass
        elif func_name.startswith("Head"):
            n = int(func_name[4:])
            evaluated = evaluated[:n]
        result = result[:func_start.start()] + evaluated + result[func_end + 1:]

    for key in sorted(replacements.keys(), key=len, reverse=True):
        result = result.replace("$" + key + "$", replacements[key])

    return result


def find_metadata_file(ts_path: Path) -> Optional[Path]:
    candidates = [
        ts_path.with_suffix(".ts.program.txt"),
        ts_path.with_suffix(".program.txt"),
        ts_path.parent / (ts_path.stem + ".program.txt"),
        ts_path.parent / (ts_path.stem + ".TXT"),
        ts_path.with_suffix(".TXT"),
    ]
    seen = set()
    for c in candidates:
        if c not in seen:
            seen.add(c)
            if c.exists():
                return c
    return None


def find_err_file(ts_path: Path) -> Optional[Path]:
    """
    TSファイルに対応するERRファイルを探索する

    @param ts_path - TSファイルのパス
    @returns ERRファイルのパス（見つからなければNone）
    """
    candidates = [
        ts_path.with_suffix(".ts.err"),
        ts_path.with_suffix(".err"),
        ts_path.parent / (ts_path.stem + ".err"),
    ]
    seen = set()
    for c in candidates:
        if c not in seen:
            seen.add(c)
            if c.exists():
                return c
    return None


def is_broken_filename(filename: str, pattern: Optional[str] = None, max_length: Optional[int] = 10) -> bool:
    """
    壊れたファイル名かどうかを判定する
    
    @param filename - ファイル名
    @param pattern - カスタム正規表現パターン
    @param max_length - ファイル名の最大長さ（拡張子除く）。デフォルト10文字。Noneで無制限
    @returns 壊れたファイル名の場合True
    """
    if pattern:
        return bool(re.search(pattern, filename))
    
    # ファイル名（拡張子除く）の長さチェック
    stem = Path(filename).stem
    if max_length is not None and len(stem) > max_length:
        return False
    
    return "~" in filename


def scan_broken_files(target_dir: Path, max_filename_length: Optional[int] = 10) -> Tuple[List[BrokenFileInfo], List[BrokenFileInfo], List[BrokenFileInfo]]:
    """
    対象ディレクトリ内の壊れたファイル名を持つファイルをスキャンする
    
    @param target_dir - 走査対象ディレクトリ
    @param max_filename_length - ファイル名の最大長さ（拡張子除く）。デフォルト10文字。Noneで無制限
    @returns (TSファイルリスト, TXTファイルリスト, ERRファイルリスト) のタプル
    """
    ts_files: List[BrokenFileInfo] = []
    txt_files: List[BrokenFileInfo] = []
    err_files: List[BrokenFileInfo] = []
    
    for file_path in target_dir.iterdir():
        if not file_path.is_file():
            continue
        
        ext = file_path.suffix.upper()
        if ext not in (".TS", ".TXT", ".ERR"):
            continue
        
        if not is_broken_filename(file_path.name, max_length=max_filename_length):
            continue
        
        last_write_time = datetime.fromtimestamp(file_path.stat().st_mtime)
        
        if ext == ".TS":
            ts_files.append(BrokenFileInfo(
                path=file_path,
                file_type="TS",
                last_write_time=last_write_time,
            ))
        elif ext == ".TXT":
            try:
                content = file_path.read_text(encoding="utf-8")
                program_info = parse_program_txt(content)
                service_id = program_info.sid if program_info else None
            except Exception:
                program_info = None
                service_id = None
            
            txt_files.append(BrokenFileInfo(
                path=file_path,
                file_type="TXT",
                last_write_time=last_write_time,
                service_id=service_id,
                program_info=program_info,
            ))
        elif ext == ".ERR":
            try:
                content = file_path.read_text(encoding="utf-8")
                service_id = parse_err_service_id(content)
            except Exception:
                service_id = None
            
            err_files.append(BrokenFileInfo(
                path=file_path,
                file_type="ERR",
                last_write_time=last_write_time,
                service_id=service_id,
            ))
    
    return ts_files, txt_files, err_files


def match_files_by_time_and_service(
    ts_files: List[BrokenFileInfo],
    txt_files: List[BrokenFileInfo],
    err_files: List[BrokenFileInfo],
    tolerance_seconds: int = 120,
) -> Dict[Path, Tuple[Optional[BrokenFileInfo], Optional[BrokenFileInfo]]]:
    """
    TSファイルとTXT/ERRファイルを時刻とServiceIDでマッチングする
    
    @param ts_files - TSファイルのリスト
    @param txt_files - TXTファイルのリスト
    @param err_files - ERRファイルのリスト
    @param tolerance_seconds - 時刻の許容誤差（秒）
    @returns TSファイルパスをキー、(TXT情報, ERR情報)を値とする辞書
    """
    result: Dict[Path, Tuple[Optional[BrokenFileInfo], Optional[BrokenFileInfo]]] = {}
    
    for ts_info in ts_files:
        ts_time = ts_info.last_write_time
        
        matched_txt: Optional[BrokenFileInfo] = None
        matched_err: Optional[BrokenFileInfo] = None
        
        for txt_info in txt_files:
            if txt_info.program_info is None:
                continue
            
            txt_end_time = txt_info.program_info.end_time
            time_diff = abs((ts_time - txt_end_time).total_seconds())
            
            if time_diff <= tolerance_seconds:
                if matched_txt is None:
                    matched_txt = txt_info
                else:
                    prev_diff = abs((ts_time - matched_txt.program_info.end_time).total_seconds())
                    if time_diff < prev_diff:
                        matched_txt = txt_info
        
        for err_info in err_files:
            err_time = err_info.last_write_time
            time_diff = abs((ts_time - err_time).total_seconds())
            
            if time_diff <= tolerance_seconds:
                if matched_txt and err_info.service_id:
                    if matched_txt.service_id == err_info.service_id:
                        matched_err = err_info
                        break
                elif matched_err is None:
                    matched_err = err_info
        
        result[ts_info.path] = (matched_txt, matched_err)
    
    return result
