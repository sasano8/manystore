# 要望: StreamCipher を manystore.crypto に新設

（対話・着手前記録）

- `manystore.crypto` に **StreamCipher** を新設。**最小限実装＋最小限動作確認**を同梱する。
- テストには実装しない（後で tests へ移す前提）。
- ストレージ実装は不要。**ファイル IO（open_reader/open_writer の AsyncFileObject）との繋ぎこみ部分の
  インターフェースが明確になればよい**のがゴール。

## 解釈・方針
- StreamCipher = **オフセット指定で部分適用できる対称バイト変換**（`transform(offset, data) -> bytes`）。
  チャンク境界非依存＝真のストリーム IO に被せられる primitive。
- 繋ぎこみ = `AsyncFileObject`（protocols.py）を包む reader/writer ラッパ（read で復号 / write で暗号化）。
  これ自体が `AsyncFileObject` を満たす＝FileStore IO にそのまま差し込める形（ストア実装はしない）。
- 動作確認 = `if __name__ == "__main__"` のインライン self-test（in-memory fake 経由の round-trip）。
