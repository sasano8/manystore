# 2026-06-27 対話：kubernetes backend / 片方向同期 / create ラッパ

ユーザーとの対話で出た要望・方針。doc-first で詰める（実装は未着手）。

## 要望1（タスク化指示あり）: ラッパ層に `create` を追加
- **内容**: `Safe*` ラッパ層に `put` とは別に `create` を実装。`exists` で確認してから put する
  「create-if-not-exists（既存なら失敗）」。
- **注意（記録）**: exists→put は **TOCTOU で racy**（並行時に二重作成しうる）。原子的 create は
  **M046 の `put_if_absent`（local=`os.link`）** が正本。本要望は*ラッパ層の利便メソッド*＝非原子で可、
  という整理。M046 と役割を分ける（ラッパ=利便/非原子・core=原子 CAS）。
- → backlog **M049**。

## 要望2（討議中）: ストレージ→ストレージの sink / sync（片方向同期）
- **モデル**: source（ローカル＝最小宣言を保持）→ sink（変換しながら受ける）への **one-way mirror**。
- **権威の分離**: source=desired の権威（人が編集）／ sink=observed の権威（同期だけが書く・人は直接書かない）。
- **3 本の流れ**: apply(push, source→sink・書き)／observe(read-only・sink を覗くだけ)／diff(drift 検出)。
  **sink→source 書き戻しは禁止**（最小宣言が補完で汚れる）＝これが「片方向」の不変条件。
- **キー1件の状態**: 未作成 / in-sync / drift（re-apply で上書き）/ orphan（source 消失→prune or keep）。
- **削除検知（論点）**: 片方向 pull はステートレスでは **両側 list の集合差**でしか検知できない
  （`to_delete = sink - source`）＝rsync --delete / kubectl --prune 方式。enumeration を避けるなら
  **state（同期済みマニフェスト）or event journal（CDC）or watch** が要る。prune は危険なので
  **「この同期が所有するキー」にスコープ（prefix/label）＋ opt-in（keep 既定）**。
- **分解**: 汎用 one-way mirror（合成層・backend 非依存）＋ pluggable comparator。変換（apply/補完）は
  sink backend の put に内包。drift comparator は (a) 部分集合比較 / (b) managedFields 所有権比較。
- → backlog **M050**（相談・doc-first）。

## 要望3（討議中）: kubernetes backend（M050 の具体 sink）
- **2 ストア**: 「kubetest ストレージ（ローカル・最小宣言）」と「kubernetes backend（apply 補完済み live）」を区別。
- **キー**: `namespace/resource_type/resource_name`（正準キーに `.yml` は含めない＝ローカル FS の符号化詳細）。
  group/version は **discovery が補完**、衝突時のみ `resource_type.group`（kubectl 流）。cluster-scoped は後回し。
- **put≠get**: put=server-side apply、get=補完済み live。**FileInfo に世代情報**
  （resourceVersion / generation / uid / creationTimestamp）を載せる（前回確定）。
- **並行制御**: resourceVersion の CAS を **M046 の参照実装**にする（`if_version` 指定で版付き update、不一致は ConflictError）。
- **検証(A)**: ローカルの `namespace/resource_type/name.yml` パスと、yml 内 `metadata.namespace`/`kind`(→type)/
  `metadata.name` の同一性検証（`validate_safe_path` と同位置＝Safe 風ラッパ `KubeManifestStore` 1 枚）。
- **クライアント/依存**: `kubernetes-asyncio` を optional extra `[k8s]`（`[server]` と同じ遅延 import 方針）。
- → backlog **M051**（相談・doc-first）。

## 要ロック（open）
- orphan policy（prune vs keep 既定）／comparator（部分集合 vs managedFields）／observe の read-only 型強制／
  mirror の置き場（合成層 vs client）／cluster-scoped の扱い。
