#!/usr/bin/env python3
"""
録画重複整理用の共通ロジック
"""

from __future__ import annotations

import hashlib
import re
import shutil
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional, Sequence

from lib.edcb import find_err_file, find_metadata_file, parse_program_txt
from lib.file_io import read_text_file_auto

_PID_SUMMARY_PATTERN = re.compile(
    r"^PID:\s+0x[0-9A-Fa-f]+\s+Total:\s*(\d+)\s+Drop:\s*(\d+)\s+Scramble:\s*(\d+)",
    re.MULTILINE,
)
_DROP_PROGRESS_PATTERN = re.compile(r"Drop:\s*(\d+)")
_FILENAME_PATTERN = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<time>\d{2}-\d{2})_(?P<service>.+?)-(?P<title>.+)$"
)
_EPISODE_PATTERNS = (
    re.compile(r"(?:#|＃)\s*0*(\d{1,4})", re.IGNORECASE),
    re.compile(r"第\s*0*(\d{1,4})\s*(?:話|回)", re.IGNORECASE),
)
_TITLE_PREFIX_PATTERN = re.compile(r"^(?:[<＜【\[].+?[>＞】\]]・|[^・]{1,12}・)")
_TITLE_TAG_PATTERN = re.compile(r"(?:\[[^\]]+\]|【[^】]+】|＜[^＞]+＞|<[^>]+>)")
_TITLE_QUOTE_PATTERN = re.compile(r"[「『].*$")
_TITLE_PUNCT_PATTERN = re.compile(r"[\s\-_・:：;；,，.。!！?？~～/／\\()（）]+")


@dataclass(frozen=True)
class ErrSummary:
    """.err ファイルから抽出した品質情報"""

    total_packets: int
    drop_count: int
    scramble_count: int
    drop_rate: Optional[float]


@dataclass(frozen=True)
class RecordingFileSet:
    """1つの録画に紐づくファイル群"""

    ts_path: Path
    metadata_path: Optional[Path]
    err_path: Optional[Path]
    title: str
    service_name: str
    normalized_service_name: str
    normalized_title: str
    series_key: str
    episode_key: str
    start_time: Optional[datetime]
    file_size: int
    err_summary: Optional[ErrSummary]


def parse_err_summary(content: str) -> Optional[ErrSummary]:
    """.err ファイルの内容から Drop 情報を抽出する"""
    packet_total = 0
    drop_total = 0
    scramble_total = 0

    for total_text, drop_text, scramble_text in _PID_SUMMARY_PATTERN.findall(content):
        packet_total += int(total_text)
        drop_total += int(drop_text)
        scramble_total += int(scramble_text)

    if packet_total > 0:
        return ErrSummary(
            total_packets=packet_total,
            drop_count=drop_total,
            scramble_count=scramble_total,
            drop_rate=drop_total / packet_total,
        )

    drop_progress = [int(match.group(1)) for match in _DROP_PROGRESS_PATTERN.finditer(content)]
    if not drop_progress:
        return None

    return ErrSummary(
        total_packets=0,
        drop_count=max(drop_progress),
        scramble_count=0,
        drop_rate=None,
    )


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).strip().lower()
    return normalized.replace("　", " ")


def _extract_episode_key(title: str) -> str:
    normalized = _normalize_text(title)
    suffix = "-final" if "[終]" in normalized or "【終】" in normalized else ""

    for pattern in _EPISODE_PATTERNS:
        match = pattern.search(normalized)
        if match:
            return f"ep{int(match.group(1)):04d}{suffix}"

    if suffix:
        return "final"

    return ""


def _build_series_key(title: str) -> str:
    normalized = _normalize_text(title)
    normalized = _TITLE_PREFIX_PATTERN.sub("", normalized)
    normalized = _TITLE_TAG_PATTERN.sub("", normalized)
    normalized = _TITLE_QUOTE_PATTERN.sub("", normalized)
    normalized = re.sub(r"(?:#|＃)\s*0*\d{1,4}.*$", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"第\s*0*\d{1,4}\s*(?:話|回).*$", "", normalized, flags=re.IGNORECASE)
    normalized = _TITLE_PUNCT_PATTERN.sub("", normalized)
    return normalized


def _extract_filename_parts(ts_path: Path) -> tuple[str, str, Optional[datetime]]:
    match = _FILENAME_PATTERN.match(ts_path.stem)
    if not match:
        return ts_path.stem, "", None

    service_name = match.group("service")
    title = match.group("title")
    try:
        start_time = datetime.strptime(
            f"{match.group('date')} {match.group('time')}",
            "%Y-%m-%d %H-%M",
        )
    except ValueError:
        start_time = None
    return title, service_name, start_time


def build_recording_file_set(ts_path: Path) -> RecordingFileSet:
    """TS ファイルから重複判定用の情報を組み立てる"""
    metadata_path = find_metadata_file(ts_path)
    err_path = find_err_file(ts_path)

    title_from_name, service_from_name, start_from_name = _extract_filename_parts(ts_path)
    title = title_from_name
    service_name = service_from_name
    start_time = start_from_name

    if metadata_path and metadata_path.exists():
        try:
            content, _ = read_text_file_auto(metadata_path)
            program_info = parse_program_txt(content)
        except Exception:
            program_info = None
        if program_info is not None:
            title = program_info.title
            service_name = program_info.service_name
            start_time = program_info.start_time

    if err_path and err_path.exists():
        try:
            content, _ = read_text_file_auto(err_path)
            err_summary = parse_err_summary(content)
        except Exception:
            err_summary = None
    else:
        err_summary = None

    return RecordingFileSet(
        ts_path=ts_path,
        metadata_path=metadata_path if metadata_path and metadata_path.exists() else None,
        err_path=err_path if err_path and err_path.exists() else None,
        title=title,
        service_name=service_name,
        normalized_service_name=_normalize_text(service_name),
        normalized_title=_normalize_text(title),
        series_key=_build_series_key(title),
        episode_key=_extract_episode_key(title),
        start_time=start_time,
        file_size=ts_path.stat().st_size,
        err_summary=err_summary,
    )


def build_recording_file_sets(target_dir: Path) -> list[RecordingFileSet]:
    """対象ディレクトリ内の TS ファイル一覧を解析する"""
    return [
        build_recording_file_set(ts_path)
        for ts_path in sorted(target_dir.iterdir(), key=lambda path: path.name)
        if ts_path.is_file() and ts_path.suffix.lower() == ".ts"
    ]


def _title_similarity(left: RecordingFileSet, right: RecordingFileSet) -> float:
    return SequenceMatcher(None, left.series_key, right.series_key).ratio()


def _full_similarity(left: RecordingFileSet, right: RecordingFileSet) -> float:
    return SequenceMatcher(None, left.normalized_title, right.normalized_title).ratio()


def _days_apart(left: RecordingFileSet, right: RecordingFileSet) -> Optional[int]:
    if left.start_time is None or right.start_time is None:
        return None
    return abs((left.start_time - right.start_time).days)


def are_duplicate_recordings(
    left: RecordingFileSet,
    right: RecordingFileSet,
    min_similarity: float,
    max_days_apart: int,
) -> bool:
    """2つの録画が同一番組の重複候補かを判定する"""
    if left.ts_path == right.ts_path:
        return False

    if left.normalized_service_name and right.normalized_service_name:
        if left.normalized_service_name == right.normalized_service_name:
            return False

    if not left.series_key or not right.series_key:
        return False

    series_similarity = _title_similarity(left, right)
    if series_similarity < min_similarity:
        return False

    if left.episode_key and right.episode_key:
        return left.episode_key == right.episode_key

    full_similarity = _full_similarity(left, right)
    days_apart = _days_apart(left, right)
    if days_apart is None:
        return full_similarity >= max(min_similarity, 0.92)

    return full_similarity >= max(min_similarity, 0.88) and days_apart <= max_days_apart


def find_duplicate_groups(
    recordings: Sequence[RecordingFileSet],
    min_similarity: float = 0.78,
    max_days_apart: int = 14,
) -> list[list[RecordingFileSet]]:
    """録画一覧から重複候補グループを抽出する"""
    if not recordings:
        return []

    parents = list(range(len(recordings)))

    def find(index: int) -> int:
        parent = parents[index]
        while parent != parents[parent]:
            parents[parent] = parents[parents[parent]]
            parent = parents[parent]
        parents[index] = parent
        return parent

    def union(left_index: int, right_index: int) -> None:
        left_root = find(left_index)
        right_root = find(right_index)
        if left_root != right_root:
            parents[right_root] = left_root

    for left_index, left in enumerate(recordings):
        for right_index in range(left_index + 1, len(recordings)):
            right = recordings[right_index]
            if are_duplicate_recordings(left, right, min_similarity, max_days_apart):
                union(left_index, right_index)

    grouped: dict[int, list[RecordingFileSet]] = {}
    for index, recording in enumerate(recordings):
        root = find(index)
        grouped.setdefault(root, []).append(recording)

    groups = [group for group in grouped.values() if len(group) >= 2]
    groups.sort(key=lambda group: group[0].ts_path.name)
    for group in groups:
        group.sort(key=lambda recording: recording.ts_path.name)
    return groups


def select_best_recording(group: Sequence[RecordingFileSet]) -> tuple[Optional[RecordingFileSet], str]:
    """重複グループから残す録画を選ぶ"""
    if not group:
        return None, "候補がありません"

    if any(recording.err_summary is None for recording in group):
        return None, ".err が見つからない録画を含むため安全のため保留しました"

    def sort_key(recording: RecordingFileSet) -> tuple[float, int, int, int, str]:
        err_summary = recording.err_summary
        assert err_summary is not None
        drop_rate = err_summary.drop_rate if err_summary.drop_rate is not None else float("inf")
        return (
            drop_rate,
            err_summary.drop_count,
            err_summary.scramble_count,
            -recording.file_size,
            recording.ts_path.name,
        )

    return min(group, key=sort_key), ""


def iter_related_files(recording: RecordingFileSet) -> list[Path]:
    """録画に紐づく既存ファイル一覧を返す"""
    related_files: list[Path] = [recording.ts_path]
    if recording.metadata_path and recording.metadata_path.exists():
        related_files.append(recording.metadata_path)
    if recording.err_path and recording.err_path.exists():
        related_files.append(recording.err_path)
    return related_files


def format_drop_rate(err_summary: Optional[ErrSummary]) -> str:
    """Drop 率を表示用の文字列に整形する"""
    if err_summary is None:
        return "-"
    if err_summary.drop_rate is None:
        return f"件数のみ {err_summary.drop_count}"
    return f"{err_summary.drop_rate * 100:.5f}%"


def compute_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """ファイルの SHA-256 を計算する"""
    digest = hashlib.sha256()
    with path.open("rb") as file_pointer:
        while True:
            chunk = file_pointer.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def ensure_path_within_root(path: Path, root_dir: Path) -> Path:
    """パスが想定ルート配下にあることを確認する"""
    resolved_root = root_dir.resolve()
    resolved_path = path.resolve(strict=False)
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise ValueError(f"ルート外のパスは使用できません: {resolved_path}")
    return resolved_path


def copy_file_with_verification(source_path: Path, destination_path: Path) -> None:
    """ファイルをコピーしてサイズとハッシュで整合性を確認する"""
    shutil.copy2(source_path, destination_path)

    source_size = source_path.stat().st_size
    destination_size = destination_path.stat().st_size
    if source_size != destination_size:
        raise ValueError(
            f"サイズが一致しません: {source_path.name} ({source_size} != {destination_size})"
        )

    if compute_sha256(source_path) != compute_sha256(destination_path):
        raise ValueError(f"ハッシュが一致しません: {source_path.name}")


def move_recording_safely(recording: RecordingFileSet, destination_dir: Path) -> list[Path]:
    """録画ファイル群を安全にコピー検証してから元を削除する"""
    resolved_destination_dir = ensure_path_within_root(destination_dir, destination_dir)
    resolved_destination_dir.mkdir(parents=True, exist_ok=True)

    related_files = iter_related_files(recording)
    copy_pairs: list[tuple[Path, Path]] = []
    for source_path in related_files:
        destination_path = ensure_path_within_root(
            resolved_destination_dir / source_path.name,
            resolved_destination_dir,
        )
        if destination_path.exists():
            raise FileExistsError(f"移動先に同名ファイルが存在します: {destination_path.name}")
        copy_pairs.append((source_path, destination_path))

    copied_destinations: list[Path] = []
    try:
        for source_path, destination_path in copy_pairs:
            copy_file_with_verification(source_path, destination_path)
            copied_destinations.append(destination_path)
    except Exception:
        for copied_path in reversed(copied_destinations):
            if copied_path.exists():
                copied_path.unlink()
        raise

    for source_path, _ in copy_pairs:
        source_path.unlink()

    return copied_destinations


def delete_recording_files(recording: RecordingFileSet) -> list[Path]:
    """録画に紐づくファイル群を削除する"""
    deleted_files: list[Path] = []
    for path in iter_related_files(recording):
        path.unlink()
        deleted_files.append(path)
    return deleted_files


def summarize_group_title(group: Sequence[RecordingFileSet]) -> str:
    """グループ表示用の代表タイトルを返す"""
    if not group:
        return ""
    representative = min(group, key=lambda recording: len(recording.title) or len(recording.ts_path.stem))
    return representative.title or representative.ts_path.stem