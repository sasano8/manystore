# 【最重要】KeyValueStoreBase(ABC) が AsyncKeyValueStore(Protocol) と完全一致しない（drift）

（対話・ユーザー指摘＝最重要事項としてタスク化）

## 事実
- `protocols.py` がインターフェース（契約）の正本＝「**契約＋既定実装の唯一の源泉**」のはず。
- `KeyValueStoreBase`（abc.ABC・L186-207）が `@abstractmethod` にしているのは **`get_or_raise` だけ**、
  あとは `get` の既定実装を足すのみ。
- `AsyncKeyValueStore`（Protocol・L55-68）は put/get_or_raise/get/iter_all/list_all/exists/delete/cp/mv/
  connect/aclose まで契約している。
- → **ABC 基底は Protocol 契約の部分ミラーで、残り 9 メソッドを宣言も強制もしない**。基底を継承した実装
  （`IpfsKeyValueStore` 等）は `get_or_raise` だけ書けばインスタンス化が通り、**残りを未実装でも黙って
  Protocol を破れる**（fail-loud にならない）。シグネチャ drift（基底と Protocol の不一致）も同根。

## なぜ重要か
- manystore の核は「protocols.py = 契約＋既定実装の唯一の源泉」。その源泉内で**契約（Protocol）と既定実装
  （ABC）が別管理で lockstep を保証する仕組みが無い**＝drift 源。要求7「fail-loud」とも反する
  （実装漏れが instantiation 時に死なない）。
- `FileStoreBase` 側も対称に要点検（open_reader/open_writer は abstract だが KVS 面の扱いは要確認）。

## 是正の選択肢（着手時に決定）
1. 基底に Protocol 全面を `@abstractmethod`（or 妥当な既定）で宣言し、未実装は instantiation 時に
   `TypeError`＝fail-loud（get_or_raise と同じ思想を全面へ）。
2. base↔Protocol の parity を **conformancer（tools・M022/M034）**で import/test 時に assert（メソッド集合・
   シグネチャ一致を機械チェック）。
3. Protocol を単一宣言とし基底がそれを参照する形へ再構成（二重定義を断つ）。

関連: [[unit-quality]]（fail-loud/源泉一元化）／conformancer M022b ／既存 M027（get_or_raise primitive 化）。
