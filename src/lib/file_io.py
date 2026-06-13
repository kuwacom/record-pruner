#!/usr/bin/env python3
"""
ファイル入出力ユーティリティ
エンコーディング自動検出・BOM対応をサポート
"""

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
