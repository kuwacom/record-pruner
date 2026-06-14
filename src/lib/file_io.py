#!/usr/bin/env python3
"""
ファイル入出力ユーティリティ
エンコーディング自動検出・BOM対応をサポート
"""

import os
from pathlib import Path


def detect_encoding(path: Path) -> str:
    """
    ファイルのエンコーディングを自動検出する
    
    BOMがあればUTF-8系、なければShift-JISやUTF-8を試す
    
    @param path - ファイルパス
    @returns 検出されたエンコーディング名
    """
    with open(path, "rb") as f:
        raw = f.read(4)
    
    # BOMチェック
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if raw.startswith(b"\xfe\xff"):
        return "utf-16-be"
    
    # BOMなしの場合、中身を読んで判定
    with open(path, "rb") as f:
        content = f.read()
    
    # UTF-8として有効かチェック
    try:
        content.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    
    # Shift-JISとして有効かチェック（日本語環境でよく使われる）
    try:
        content.decode("shift_jis")
        return "shift_jis"
    except UnicodeDecodeError:
        pass
    
    # CP932（Shift-JISの拡張）を試す
    try:
        content.decode("cp932")
        return "cp932"
    except UnicodeDecodeError:
        pass
    
    # デフォルトはUTF-8（BOMなし）
    return "utf-8"


def read_text_file_auto(path: Path) -> tuple[str, str]:
    """
    テキストファイルを自動検出で読み込む
    
    @param path - ファイルパス
    @returns (内容, 検出されたエンコーディング) のタプル
    """
    encoding = detect_encoding(path)
    return path.read_text(encoding=encoding), encoding


def read_text_file(path: Path, encoding: str = "utf-8-sig") -> str:
    """
    テキストファイルを読み込む
    デフォルトは utf-8-sig（BOM付きUTF-8に対応）
    """
    return path.read_text(encoding=encoding)


def write_text_file(path: Path, content: str, encoding: str = "utf-8-sig") -> None:
    """
    テキストファイルを書き込む
    デフォルトは utf-8-sig（BOM付きUTF-8で書き込み）
    """
    path.write_text(content, encoding=encoding)


def write_text_file_preserve_timestamp(path: Path, content: str, encoding: str = "utf-8-sig") -> None:
    """
    テキストファイルを書き込み、タイムスタンプ（作成日・更新日）を保持する
    """
    # 現在のタイムスタンプを保存
    stat = path.stat()
    mtime = stat.st_mtime
    atime = stat.st_atime
    ctime = getattr(stat, "st_ctime", None)

    # ファイルを書き込み
    path.write_text(content, encoding=encoding)

    # タイムスタンプを復元
    if os.name == "nt" and ctime is not None:
        # Windows: 作成時刻も含めて復元
        import ctypes
        from ctypes import wintypes

        def _to_filetime(unix_time: float) -> wintypes.FILETIME:
            timestamp = int((unix_time + 11644473600) * 10000000)
            return wintypes.FILETIME(timestamp & 0xFFFFFFFF, timestamp >> 32)

        handle = ctypes.windll.kernel32.CreateFileW(
            str(path), 0x100, 0, None, 3, 0x80, None
        )
        if handle != -1:
            ctime_ft = _to_filetime(ctime)
            mtime_ft = _to_filetime(mtime)
            atime_ft = _to_filetime(atime)
            ctypes.windll.kernel32.SetFileTime(
                handle, ctypes.byref(ctime_ft), ctypes.byref(atime_ft), ctypes.byref(mtime_ft)
            )
            ctypes.windll.kernel32.CloseHandle(handle)
    else:
        os.utime(path, (atime, mtime))

