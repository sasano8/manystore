# M028 設計プラン: HTTP の context を ArrayStorage に寄せる（contexts = ArrayStorage 第一階層）

> ユーザー要望（2026-06-24, 対話）: 「HTTP API の `contexts` 概念は ArrayStorage の第一階層のことだ。
> だから ArrayStorage をそのまま公開すればいいはず。計画してタスクに積んでおいて。」
> **本ドキュメントは設計のみ。実装・コミットはしない**（doc-first 合意 → 別サイクルで着手）。

## 観察（要望の核心は正しい）

HTTP server 層の `StorageService`（`implement/service.py`）は、config の各 context を
`_stores: dict[context_name, SafeKeyValueStore]` に保持し、`(context, key)` の 2 引数で
`self._stores[context].op(key)` に振り分けている。これは **第一セグメントで mount 先へ振り分ける**
合成ストア — すなわち `ArrayKeyValueStore`（`array_storage.py`）が既に持つ機能の再実装である。

| StorageService（現状） | ArrayKeyValueStore（既存） |
|---|---|
| `_stores: dict[name, KVS]` | `_mounts: dict[name, KVS]` |
| `self._stores[ctx].put(key, v)` | `put("<name>/<subkey>", v)` → `_route` で分解 |
| `list_contexts()` の name 列 | `mounts()`（名前順） |
| `list_entries` が context 横断は無し（context 単位） | `iter_all()` が各 mount を `<name>/` で prefix して横断 |
| cp/mv は context 内のみ | `cp`/`mv` が mount 跨ぎ（get→put / copy→delete）も対応 |

つまり **「context 振り分け＋context 横断列挙＋context 跨ぎ cp/mv」は ArrayStorage が一段上等の実装で
既に提供済み**。StorageService はそれを薄く焼き直している。

## 方針（提案）

`StorageService` の **routing/composition を ArrayKeyValueStore に委譲**する。各 context を
`SafeKeyValueStore` で包んで ArrayStorage に `mount(context, store)` し、HTTP の `(context, key)` は
`array["{context}/{key}"]` に合成して 1 本の KVS 操作へ落とす。

```
StorageService.connect():
    array = ArrayKeyValueStore()
    for name, cc in config.contexts:
        raw  = create_key_value_store(cc.backend, **cc.opts)
        safe = SafeKeyValueStore(raw)
        await array.mount(name, safe)          # mount が connect も担う
        # watcher は safe を直接掴んで張る（下記参照）
    self._array = array
```

得られる帰結:
- `get/put/delete/exists` → `array["{ctx}/{key}"]` 一発。`_store(context)` の dict 引きが消える。
- `list_entries(ctx, prefix)` → ArrayStorage の `iter_all()` は既に `<name>/` prefix 付き。
  `"{ctx}/{prefix}"` で startswith 絞り → context 内列挙。横断列挙（全 context）も `iter_all()` で自然に出る。
- `list_contexts()` の名前部分 ≈ `array.mounts()`。

## 関心の分離（ArrayStorage に持ち込まない＝core を汚さない）

projectbrief「最小・汎用に保つ（YAGNI）／利用側都合で core IF を拡張しない」より、以下は
**ArrayStorage に入れず service 層に残す**（HTTP/ポリシー固有の関心）:

1. **`writable`（read-only）ポリシー強制** — context ごとの書込可否。ArrayStorage は権限概念を持たない。
   → service が config を引いて `_require_writable` を維持。
2. **context メタデータ**（`backend` 名・`writable`）= `/contexts` 一覧用。ArrayStorage の `mounts()` は
   名前しか返さない。→ service が config 由来のメタ表を持ち続ける（ArrayStorage に writable/backend を
   足すのは core 汚染なので**しない**）。
3. **per-context PollingWatcher ＋ WS fan-out** — ArrayStorage は監視を持たない。`_mounts` は private で
   外から mount 先 store を取れない。→ service が **各 context の `safe` store の参照を保持**し、それに
   watcher を張る（ArrayStorage には同じ store を mount する＝参照を二重に持つだけ、store 実体は 1 つ）。
4. **`featured` / `default_context`** — ビュー設定。service が保持。
5. **`SafeKeyValueStore`（キー検証）** — 各 mount を包む（現状どおり）。ArrayStorage 全体を包むのではなく
   mount 単位で包むことで、検証を backend 直前に効かせ context 分離を保つ。

→ 実質、**ArrayStorage は純粋に routing/composition の道具として使い、ポリシー・監視・メタは service が薄く上載せ**。

## 解決すべき設計上の緊張（doc-first で詰める）

- **空キー / context 境界**: ArrayStorage の `_route` は `<name>/<subkey>` で **両方非空**を要求し、
  bare な context 名（subkey 無し）は `exists` だけ dir 扱いで True を返す。HTTP は context と key を
  別パラメータで受けるので、`key=""`（context ルート）の扱いを決める（現状 service の挙動と差が出ないか）。
- **`list_all` の既定 limit**: ArrayStorage `list_all(limit=10)` と service `list_entries(limit=1000)` で
  既定が違う。prefix 絞りは ArrayStorage の `iter_all` に無いので **service 側で startswith 絞り＋limit**を
  維持（現状ロジックをそのまま `array.iter_all()` の上で回すだけ）。
- **watcher 用の mount 参照**: service が `safe` を自前 dict にも持つか、ArrayStorage に「mount 先 store を
  名前で返す」read アクセサ（`get_mount(name)` 等）を足すか。**前者（service が参照を持つ）が core を
  汚さず素直**。
- **エラー写像**: 不明 context は現状 `ContextNotFound`（KeyError 派生）。ArrayStorage は不明 mount を
  素の `KeyError` にする。HTTP の 404 写像を保つため、service 層で `KeyError`→`ContextNotFound` に正規化、
  または `mounts()` で事前判定。

## スコープと不変条件

- **内部リファクタ**: 変更は `implement/service.py` が主。**core IF（KeyValueStore/FileStore）不変**、
  **HTTP ルート（`/contexts/...`）不変**、**振る舞い保存**（既存 `tests/ui` が緑のまま）。
- 新依存ゼロ。`make check` 緑維持。
- 旨み: DRY（合成プリミティブを 1 本に集約）＋概念統一（context = ArrayStorage mount）。

## 発展（follow-up・別タスク候補）

要望「**そのまま公開**」をさらに進めると、**ArrayStorage 自体を HTTP に露出**して context を静的 config
ではなく **動的 mount/unmount** にできる（`POST /contexts`＝mount / `DELETE /contexts/{name}`＝unmount）。
ただし mount に backend 資格情報を HTTP から渡す＝認証/安全性の設計が要る（M011 認証と連動）。
本タスク（M028）は **静的 context を ArrayStorage に寄せる behavior-preserving リファクタ**に限定し、
動的 mount 公開は **M028b（相談・要設計）**として分離する。

## 未決事項（着手前にユーザー確認）

1. behavior-preserving リファクタとして進めてよいか（HTTP 契約・テスト数を変えない）。
2. watcher 参照は service 保持で確定してよいか（ArrayStorage に read アクセサを足さない）。
3. 動的 mount 公開（M028b）は今回スコープ外で合意か。
