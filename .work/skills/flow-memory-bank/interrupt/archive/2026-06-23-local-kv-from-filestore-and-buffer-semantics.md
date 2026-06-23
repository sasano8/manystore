# ユーザー要望（対話・2026-06-23）— バッファ性の整理と Local KV の派生方向

> M025（buffer 性での名前空間再編）/ M026（stream IF）の続き。設計クリアリフィケーション＋具体要望 1 件。

## バッファ性の方針（理解の確認）

- バッファする境界があるのは理解。**バッファされないのは「バッファ無しストレージをそのまま露出させた場合」だけでよい。**
- 本プロダクトの目的は**インターフェースの整理**。バッファ層を隠蔽したストレージが**真のストリーム性を提供できないのは仕方ない**。
- ただし、**サーバーとして提供せず、クライアント側でラップすれば真の性能を得られる。真髄はクライアントプログラムにある。**

## バッファ性マトリクス

| 構成 | バッファ |
|------|----------|
| KV | 基本バッファされる |
| バッファ無しストレージ | バッファされない。**ただし manystore でバックエンドをリレーしている場合はその限りでない** |
| KV → バッファ無しストレージ（KVS を FileStore 化＝既存 `KeyValueFileStore`） | KV 層でバッファあるため、バッファ無しストレージは**みせかけ** |
| バッファ無しストレージ → KV（FileStore を KVS 化＝新 `KeyValueFromFileStore`） | KV 層でバッファあり |

→ 要するに **KV は本質的にバッファ概念／FileStore（ストリーム）はバッファ無し概念**。どちら向きに被せても
KV が現れる所にバッファが現れるのは当然で許容。

## 具体要望

- **ローカルストレージの KV は `KeyValueFromFileStore` で、メイン実装は `LocalFileStore` 側にあるのがよい。**
  （＝`LocalFileStore` を真実の実装にし、`LocalKeyValueStore` はそこから派生したバッファ KV ビューにする。
  既存の `KeyValueFileStore`〔KVS→FileStore〕の対称な逆 `KeyValueFromFileStore`〔FileStore→KVS〕を新設）

## 設計上の論点（着手前 deep think 用・コード確認済み）

- `KeyValueStore` Protocol = put/get/iter/list/exists/delete/cp/mv/connect/aclose。
- `FileStore` Protocol = open_reader/open_writer/connect/aclose のみ。
- → **FileStore には list/exists/delete/iter が無い**。`KeyValueFromFileStore(FileStore)` は get/put しか派生できない。
  メイン実装を LocalFileStore に寄せるには、(a) FileStore を list/exists/delete で拡張、(b) LocalFileStore が
  Protocol を超える追加メソッドを持つ、(c) Local は metadata 操作用に小さな KVS を残し IO だけ委譲、のいずれか要決定。
  → projectbrief「最小・汎用/YAGNI」と緊張＝**doc-first 合意してから着手**。
