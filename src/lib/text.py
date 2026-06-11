import re

# Windows でファイル名に使用できない文字の正規表現パターン
WINDOWS_FORBIDDEN_CHARS = r'[\\/:*?"<>|]'


def escape_filename(filename: str, replacement: str = "_") -> str:
    """
    Windows ファイル名禁止文字を指定文字で置換する

    @param filename - 元のファイル名
    @param replacement - 置換文字（デフォルト: _）
    @returns エスケープ後のファイル名
    """
    return re.sub(WINDOWS_FORBIDDEN_CHARS, replacement, filename)
