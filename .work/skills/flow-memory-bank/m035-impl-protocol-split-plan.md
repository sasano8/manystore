# M035: 実装 / プロトコル分離（src→dest 移動マップ）

要求6（interrupt aaa.md）＝「IF・プロトコル的なものを抽出して `manystore` 直下へ／実装群は別ディレクトリへ」。
**IDE の move-symbol / move-file リファクタで import は自動追従**する前提。本書は何をどこへ動かすかの正本。

## 目標レイアウト

```
manystore/
  protocols.py        # NEW（root）＝契約（Protocol / 型）を 1 か所に集約
  stores/             # NEW subpackage＝横断的な「実装の building block」
    __init__.py
    base.py           # async_storage.py の実装部分
    array.py          # array_storage.py
    safe.py           # safe_path.py
    sync_bridge.py    # async_to_sync_storage.py
  backends/           # 変更なし（既にバックエンド実装の subpackage）
  kv.py / file.py     # facade＝import 元が変わるだけ。公開 API（manystore.kv.X）は不変
```

> **subpackage 名は `stores/` を推奨**（`impl/` は既存 `implement/`＝UI/service 層と紛らわしいので避ける。
> 代替案 `core/`／`_impl/`）。**この命名はユーザー確定待ちの 1 点**。

## 移動マップ（symbol → dest）

### A. `manystore/protocols.py`（NEW・契約のみ）

| 元ファイル | シンボル | 種別 |
|---|---|---|
| async_storage.py | `FileInfo` | TypedDict（データ契約） |
| async_storage.py | `KeyValueStore` | Protocol |
| async_storage.py | `SupportsPrefixListing` | Protocol（optional capability） |
| async_storage.py | `FileObject` | Protocol |
| async_storage.py | `FileStore` | Protocol |
| sync_storage.py | `SyncKeyValueStore` | Protocol |
| sync_storage.py | `SyncFileObject` | Protocol |
| sync_storage.py | `SyncFileStore` | Protocol |

→ **`sync_storage.py` は中身が全部 Protocol なので空になる＝削除**（全行が protocols.py へ）。

### B. `manystore/stores/base.py`（← async_storage.py の実装部分）

| シンボル | 種別 |
|---|---|
| `KeyValueStoreBase` | ABC（get 既定実装＋abstract get_or_raise）※判断ポイント①参照 |
| `_take` / `_atomic_write_bytes` / `_kv_copy` / `_kv_move` | 内部ヘルパ |
| `iter_prefix`（ディスパッチ） / `scan_prefix` | capability ヘルパ |
| `_KvReadFileObject` / `_KvWriteFileObject` | buffer 合成 FileObject |
| `KeyValueFileStore` / `KeyValueFromFileStore` | 2 方向アダプタ |

→ **`async_storage.py` は A＋B で全シンボルが移るので空になる＝削除**。

### C. 丸ごと移動（whole-file・分割なし）

| 元ファイル | → dest | 含むシンボル |
|---|---|---|
| array_storage.py | stores/array.py | `ArrayKeyValueStore` / `DownloadCache` / `DEFAULT_CACHE_DIR` |
| safe_path.py | stores/safe.py | `validate_safe_path` / `SafeKeyValueStore` / `SafeFileStore` |
| async_to_sync_storage.py | stores/sync_bridge.py | `AsyncToSyncKeyValueStore` |

## import 追従（IDE が自動・確認用）

- `backends/*.py`：`FileInfo`/`FileObject`→`protocols`、`KeyValueStoreBase`/`_take`/`scan_prefix`/
  `_Kv*FileObject`/`KeyValueFromFileStore`→`stores.base`。
- `connect.py` / `conformance.py` / `implement/*` / `gateway/*` / `server/*` / `client/*`：旧
  `from .async_storage import …` / `from .safe_path import …` / `from .array_storage import …` を新パスへ。
- `kv.py` / `file.py`（facade）：再エクスポート元のみ変更。**`__all__`・公開名は不変**＝外部影響ゼロ。
- 後方互換シム（旧 `async_storage.py` 等を re-export で残す）は**作らない**＝未リリース・内部のみ。facade が
  公開 API を固定するので破壊なし。

## 判断ポイント（着手前に確定したい）

1. **`KeyValueStoreBase` の置き場所**：ABC だが `get()` の具体ロジックを持つ＝本マップでは **stores/base.py（実装）**
   に置いた。「ABC も契約面」とみなすなら protocols.py 寄せも可（その場合 protocols.py が abc に依存）。**推奨=実装側**。
2. **subpackage 名**：`stores/`（推奨）／`core/`／`_impl/`。
3. **protocols を 1 ファイルにまとめるか**：async/sync を 1 つの `protocols.py` に集約（推奨）／`protocols.py`＋
   `protocols_sync.py` に分割。

## 進め方（behavior-preserving・段階）

各ステップ後に `make check` 緑を保つ：(1) protocols.py 作成＋Protocol 移動、(2) stores/ 作成＋実装移動、
(3) facade/各所 import 追従、(4) 空ファイル削除。1 ステップ=1 コミット可。core IF の振る舞いは不変。
