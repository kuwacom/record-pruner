# record-pruner

録画ファイル群を整理するスクリプト集

## 概要

テレビ録画などで生成されたファイル群を整理するための CLI ツール集
ファイル名のフォルダ誤分割修正、EDCB 出力バグによるファイル名復旧など、
録画ファイル特有の問題に対処する

## セットアップ

- [uv](https://docs.astral.sh/uv/) が必要

```bash
uv sync
```

## スクリプト一覧

| スクリプト名 | 説明 |
|-------------|------|
| `fix-split-filenames` | フォルダ誤分割された録画ファイルを結合して元のファイル名に復元する |
| `recover-filenames` | EDCB 出力バグで壊れたファイル名をメタデータから復旧する |

---

## fix-split-filenames

ファイル名に含まれる全角括弧などの影響で、録画ソフトがファイル名を途中でフォルダとファイルに分割してしまったケースを修正する

win でも linux でも使えます

### 機能

- フォルダ名とファイル名を結合して元のファイル名を復元
- フォルダ名とファイル名の間に任意の文字列を挿入可能
- Windows 禁止文字を自動でエスケープ
- 処理後に空フォルダを自動削除（オプションで残すことも可能）
- dry-run モードで事前確認が可能

### オプション一覧

| オプション | 短縮形 | デフォルト | 説明 |
|-----------|--------|-----------|------|
| `--target-dir` | `-t` | 必須 | 対象ディレクトリのパス |
| `--dry-run` | `-n` | `false` | 実際に移動せずシミュレーションのみ実行 |
| `--escape-with` | - | `"_"` | Windows 禁止文字のエスケープ文字 |
| `--join-with` | - | `""` | フォルダ名とファイル名の間に挿入する文字列 |
| `--keep-empty` | - | `false` | 空になったフォルダを削除しない |

### 使用例

```bash
# dry-run で確認（必ず最初に実行することを推奨）
uv run fix-split-filenames -t "F:\anime\2025年-夏" --dry-run

# 実際に実行
uv run fix-split-filenames -t "F:\anime\2025年-夏"

# フォルダ名とファイル名の間に "_" を挿入
uv run fix-split-filenames -t "F:\anime\2025年-夏" --join-with "_"

# 空フォルダを残す
uv run fix-split-filenames -t "F:\anime\2025年-夏" --keep-empty

# エスケープ文字を変更
uv run fix-split-filenames -t "F:\anime\2025年-夏" --escape-with "-"
```

### 処理の流れ

1. 対象ディレクトリ内のサブディレクトリを検索
2. 各サブディレクトリ内のファイルを取得
3. `フォルダ名 + join_with + ファイル名` で結合後のファイル名を生成
4. Windows 禁止文字が含まれる場合はエスケープ
5. ファイルを親ディレクトリに移動
6. 空になったフォルダを削除（`--keep-empty` 指定時は残す）

---

## recover-filenames

EDCB の出力バグなどでファイル名が Windows 8.3 短いファイル名（例: `2A0UJI~T.TS`）に壊れてしまった録画ファイルを、隣接する `.program.txt` メタデータから番組情報を読み取り、正しいファイル名に復旧する

Write_Default.so / RecName_Macro.so と同じ形式で命名できる

### 機能

- `.program.txt` メタデータから番組情報を自動抽出
- RecName_Macro.so 互換のマクロ変数でファイル名パターンを指定可能
- `.env` ファイルで設定を使い回せる（引数との同時設定時は引数が優先）
- メタデータファイルも新しいファイル名に付け替え可能
- dry-run モードで事前確認が可能

### マクロ変数一覧

| マクロ | 置換内容 | 例 |
|--------|---------|-----|
| `%YYYY%` | 年（4桁） | `2026` |
| `%YY%` | 年（2桁） | `25` |
| `%MM%` | 月（2桁ゼロ埋め） | `07` |
| `%M%` | 月 | `7` |
| `%DD%` | 日（2桁ゼロ埋め） | `13` |
| `%D%` | 日 | `13` |
| `%hh%` | 時（2桁ゼロ埋め） | `23` |
| `%h%` | 時 | `23` |
| `%mm%` | 分（2桁ゼロ埋め） | `30` |
| `%m%` | 分 | `30` |
| `%ss%` | 秒（2桁ゼロ埋め） | `00` |
| `%s%` | 秒 | `0` |
| `%w%` | 曜日（日本語） | `日` |
| `%ServiceName%` | サービス名（放送局名） | `BS11イレブン` |
| `%EventName%` | 番組名 | `アークナイツ【焔燼曙明/RISE FROM EMBER】` |
| `%Title2%` | サブタイトル | `#18` |
| `%SID10%` | サービスID（10進数） | `211` |
| `%SID16%` | サービスID（16進数） | `D3` |

### オプション一覧

| オプション | 短縮形 | デフォルト | 説明 |
|-----------|--------|-----------|------|
| `--target-dir` | `-t` | `.env` または必須 | 対象ディレクトリのパス |
| `--dry-run` | `-n` | `false` | 実際にリネームせずシミュレーションのみ実行 |
| `--extension` | - | `.ts` | 対象ファイルの拡張子 |
| `--macro-pattern` | - | `$SDYYYY$-$SDMM$-$SDDD$_$STHH$-$STMM$_$ServiceName$-$EventName$` | 出力ファイル名のマクロパターン |
| `--filename-pattern` | - | `None` | 壊れたファイル名検出用カスタム正規表現 |
| `--escape-with` | - | `"_"` | Windows 禁止文字のエスケープ文字 |
| `--keep-metadata` | - | `true` | メタデータファイルも付け替える |
| `--no-keep-metadata` | - | - | メタデータファイルは元のまま |

### .env 設定

`.env.example` をコピーして `.env` を作成し、以下の変数を設定できる

```bash
# 対象ディレクトリ
RECOVER_TARGET_DIR=F:\anime\2025年-夏

# マクロパターン
RECOVER_MACRO_PATTERN=$SDYYYY$-$SDMM$-$SDDD$_$STHH$-$STMM$_$ServiceName$-$EventName$

# ファイル拡張子
RECOVER_EXTENSION=.ts

# Windows 禁止文字のエスケープ文字
RECOVER_ESCAPE_WITH=_

# dry-run モード
RECOVER_DRY_RUN=false

# メタデータも付け替える
RECOVER_KEEP_METADATA=true
```

### 使用例

```bash
# dry-run で確認（必ず最初に実行することを推奨）
uv run recover-filenames -t "F:\anime\2025年-夏" --dry-run

# 実際に実行
uv run recover-filenames -t "F:\anime\2025年-夏"

# マクロパターンを変更（日付のみのファイル名）
uv run recover-filenames -t "F:\anime\2025年-夏" --macro-pattern "$SDYYYY$$SDMM$$SDDD$_$STHH$$STMM$_$EventName$"

# カスタム正規表現でファイル名を検出
uv run recover-filenames -t "F:\anime\2025年-夏" --filename-pattern "~[0-9A-Z]"

# メタデータファイルは元のままにする
uv run recover-filenames -t "F:\anime\2025年-夏" --no-keep-metadata
```

### 処理の流れ

1. 対象ディレクトリ内のファイルを走査
2. 壊れたファイル名（デフォルトで `~` を含む）を検出
3. 対応する `.program.txt` メタデータファイルを探索
4. メタデータを解析して番組情報を抽出
5. マクロパターンで新しいファイル名を生成
6. Windows 禁止文字が含まれる場合はエスケープ
7. ファイルをリネーム
8. オプションに応じてメタデータファイルも付け替え

---

## プロジェクト構成

```
record-pruner/
├── src/
│   ├── lib/              # 汎用関数・ロジック
│   └── scripts/          # CLI スクリプト
├── .env.example          # 環境変数設定例
├── pyproject.toml
├── uv.lock
└── README.md
```

## よくあるトラブルシューティング

### PermissionError が発生する時

ファイルが他のプロセス（メディアサーバーや録画ソフトなど）で使用中の可能性がある
Plex / Jellyfin / Emby などを一時停止してから再実行してください

### メタデータファイルが見つからない

EDCB の設定で「番組情報をファイルに出力する」が有効になっているか確認してください
設定箇所: EDCB設定 -> 録画 -> 出力プラグイン -> Write_Default.so -> 「番組情報を出力する」

### .env の設定が反映されない

`.env` ファイルがプロジェクトルートに配置されているか確認してください
引数と同時に設定した場合、引数が優先されます
