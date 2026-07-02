# Backend レジストリ / プラグイン

manystore は「backend 名 → ストア生成」を **1 つのレジストリ**で解決する（fsspec の
`known_implementations` + entry-point 発見に相当）。これは後続の名前 URL 取得
（`s3://` / `manystore://` …）や config からのストア復元、外部プラグイン backend
（k8s 等）の土台になる。

## 解決モデル：flat lookup ＋ tier/origin 分離 ＋ clobber 保護

- **lookup は flat**：名前空間は 1 本。`s3` はどの経路で登録されても `s3` で引ける
  （利用者が `builtin:s3` のような接頭辞を書かずに済む＝clean な URL UX のため）。
- **登録は 3 tier に分かれ、出自（origin）を記録**する。builtin 名は予約され、plugin が
  黙って乗っ取れない（supply-chain 安全）。

| tier | origin | 権限 |
|------|--------|------|
| **builtin**（同梱） | `builtin` | 最初に seed。以後**予約**＝shadow 不可 |
| **entry-point**（plugin） | `entry-point:<dist>` | **新規名の追加のみ可**。既存名（builtin/他 EP）への衝突は**拒否＋warn**（既存が勝つ） |
| **programmatic**（`register_backend`） | `programmatic` | 明示コール。既存名は **`clobber=True` のときだけ**上書き可 |

予約名を正当に差し替えたいケース（自前 S3 実装に載せ替える等）は
`register_backend(..., clobber=True)` という**明示の一手**を必ず経由する。

## データモデル

```python
@dataclass(frozen=True)
class BackendSpec:
    name: str
    factory: Callable[..., AsyncStore]   # 未接続の full Store（値 API + IO API）を作る単一 factory（M071）
    origin: str = "programmatic"         # "builtin" | "entry-point:<dist>" | "programmatic"
```

M071 で backend は 1 クラス＝factory も 1 本に統合した（旧 `kv_factory` / `file_factory` は廃止）。
factory は `**opts`（backend 固有パラメータ）を受け、**未接続**の full Store を返す。接続は
呼び出し側（`connecting` / 顔の `open_async_*`）が担う（レジストリは「構築」だけ）。

## 公開 API

```python
from manystore import register_backend, BackendSpec, list_backends, get_backend_spec

register_backend("mybackend", factory=make_store)  # programmatic（1 backend = 1 factory）
spec = get_backend_spec("s3")        # builtin→(遅延)entry-point の順に解決。無ければ ValueError（候補一覧付き）
for s in list_backends():            # 由来つき一覧（診断・将来 CLI）
    print(s.name, s.origin)
```

`create_unsafe_store(backend, **opts)` はレジストリの薄いラッパ
（`get_backend_spec(backend).factory(**opts)`）＝1 つの full Store を返す。**後方互換のためシグネチャ・名前は据え置き**
（既存の `local_dir=` / `s3_bucket=` / `http_base_url=` … の flat kwargs はそのまま動く。
backend ネイティブな opts への整理は M069 の URL 設計で回収する）。

## 同梱 backend（builtin）

| name | 値 API | IO API | 主な opts（暫定・flat kwargs） |
|------|-----|-----------|------|
| `memory` | ✓ | ✓ | （なし・揮発） |
| `local` | ✓ | ✓ | `local_dir`（**必須**） |
| `s3` | ✓ | ✓ | `s3_bucket` / `s3_endpoint` / `s3_region` / `s3_access_key` / `s3_secret_key` / `s3_addressing_style` |
| `nats` | ✓ | ✓ | `nats_url` / `nats_bucket` |
| `http` | ✓ | ✓（read-only） | `http_base_url` / `http_headers` |
| `manystore` | ✓ | — | `base_url` / `context`(=bucket) / `headers` |

`manystore` は manystore 自身の HTTP サービスを喋る `RemoteStore`（`client/` に在中）を
seed 登録したもの。`manystore://host/bucket` を他 backend と**同格**に扱うためのエントリで、
コードは `client/` に据え置く（横断 SDK `ManystoreClient` は 1 ストアに収まらないので**非登録**）。
重い依存（aiobotocore / nats / httpx / client）は各 factory 内で**遅延 import** する。

## プラグインの書き方（entry-point）

配布パッケージ側で group `manystore.stores` に登録する。**EP 名＝backend/scheme 名**。

```toml
# 配布側 pyproject.toml
[project.entry-points."manystore.stores"]
foo = "foo_manystore:get_backend_spec"
```

```python
# foo_manystore/__init__.py
from manystore import BackendSpec

def get_backend_spec() -> BackendSpec:
    return BackendSpec(name="foo", factory=_make_foo_store)
```

- EP のターゲットは **`() -> BackendSpec` の呼び出し可能**（または `BackendSpec` 直）。
- **発見は遅延**：レジストリに無い名前が初めて要求されたとき（`get_backend_spec` の miss）
  または `list_backends()` 時に一度だけ entry-point を走査する。壊れた plugin の import 失敗は
  warn して握り、他 backend を巻き込まない。
- builtin と同名の EP は**拒否＋warn**（builtin が勝つ）。

## 非対象・後続

- URL 文法（`s3://` / `local://.` / `manystore://` …）と bucket 粒度の統一は **M069**。
- `manystore store init` と config からのストア復元は **M070**。
- k8s secrets backend は本機構に乗る plugin/optional backend（**M051**）。
