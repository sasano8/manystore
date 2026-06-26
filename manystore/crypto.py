"""ストリーム暗号と、ファイル IO（[AsyncFileObject]）への繋ぎこみ。

このモジュールの主眼は **インターフェースの明確化**であって暗号強度ではない。本プロジェクトの
原則（核は primitive 側／ラッパは 1 枚で backend には触れない）に倣い、次の 2 層に分ける:

1. **[StreamCipher]** … *オフセット指定で部分適用できる対称バイト変換*。`transform(offset, data)`
   は「先頭から `offset` バイト目に置かれた `data`」を変換する。**チャンク境界に依存しない**
   ＝真のストリーム IO に被せられる primitive。XOR キーストリーム系では encrypt == decrypt（対称）。

2. **[CipherReader] / [CipherWriter]** … 既存の [AsyncFileObject]（`open_reader`/`open_writer` の
   戻り値）を 1 枚だけ包み、read で復号 / write で暗号化する。これ自体が [AsyncFileObject] を満たす
   ＝**FileStore の IO にそのまま差し込める繋ぎこみ点**（ストア本体には手を入れない）。

ストレージ実装（暗号化 FileStore）はここでは提供しない。利用側は backend の `open_reader`/
`open_writer` が返したオブジェクトを下記ラッパで包むだけでよい:

    async with await store.open_writer(name) as raw:
        async with CipherWriter(raw, cipher) as enc:
            await enc.write(plaintext)        # 暗号文として書かれる
"""

from typing import Protocol, runtime_checkable

from manystore.exceptions import UnsupportedOperation
from manystore.protocols import AsyncFileObject


@runtime_checkable
class StreamCipher(Protocol):
    """オフセット指定で部分適用できる対称バイト変換（ストリーム暗号の primitive）。

    `transform(offset, data)` は、平文/暗号文ストリームの先頭から `offset` バイト目に位置する
    `data` を変換して返す。**呼び出しはチャンク境界に依存しない**（同じバイト列なら、どう分割して
    渡しても結合結果は一致する）。XOR キーストリーム系の対称暗号では暗号化と復号が同一操作なので、
    [CipherReader]（復号）と [CipherWriter]（暗号化）は同じ `transform` を使う。
    """

    def transform(self, offset: int, data: bytes) -> bytes: ...


class XorStreamCipher:
    """繰り返し鍵 XOR による最小実装（[StreamCipher] の参照実装）。

    ⚠️ **暗号学的に安全ではない**（鍵が周期的に再利用される）。インターフェースを動かして
    確認するための placeholder であり、本番用途には KDF + AEAD ベースの実装へ差し替える。
    オフセット可変なのが要点＝`transform(offset, data)` を任意のチャンク列に適用できる。
    """

    def __init__(self, key: bytes) -> None:
        if not key:
            raise ValueError("key must be non-empty")
        self._key = key

    def transform(self, offset: int, data: bytes) -> bytes:
        key = self._key
        n = len(key)
        return bytes(b ^ key[(offset + i) % n] for i, b in enumerate(data))


class CipherReader:
    """[AsyncFileObject]（読み取り）を包み、read 時にストリーム復号するラッパ。

    自身も [AsyncFileObject] を満たす（write は read-only ストリームなので拒否）。内部で読み出し
    オフセットを保持し、下層が返したチャンクに `cipher.transform(offset, chunk)` を適用する。
    """

    def __init__(self, inner: AsyncFileObject, cipher: StreamCipher) -> None:
        self._inner = inner
        self._cipher = cipher
        self._offset = 0

    async def read(self, size: int = -1) -> bytes:
        chunk = await self._inner.read(size)
        out = self._cipher.transform(self._offset, chunk)
        self._offset += len(chunk)
        return out

    async def write(self, data: bytes) -> int:
        raise UnsupportedOperation("read-only stream")

    async def close(self) -> None:
        await self._inner.close()

    async def __aenter__(self) -> CipherReader:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()


class CipherWriter:
    """[AsyncFileObject]（書き込み）を包み、write 時にストリーム暗号化するラッパ。

    自身も [AsyncFileObject] を満たす（read は write-only ストリームなので拒否）。内部で書き込み
    オフセットを保持し、渡された平文チャンクに `cipher.transform(offset, chunk)` を適用してから
    下層へ書く。
    """

    def __init__(self, inner: AsyncFileObject, cipher: StreamCipher) -> None:
        self._inner = inner
        self._cipher = cipher
        self._offset = 0

    async def read(self, size: int = -1) -> bytes:
        raise UnsupportedOperation("write-only stream")

    async def write(self, data: bytes) -> int:
        enc = self._cipher.transform(self._offset, data)
        self._offset += len(data)
        return await self._inner.write(enc)

    async def close(self) -> None:
        await self._inner.close()

    async def __aenter__(self) -> CipherWriter:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()


def _selftest() -> None:
    """最小限の動作確認（テストには置かない＝後で tests へ移す。インライン self-test）。

    in-memory の fake [AsyncFileObject] を使い、CipherWriter で暗号化書き込み → CipherReader で
    復号読み出しの round-trip と、チャンク分割しても結果が一致する（境界非依存）ことを確認する。
    """
    import asyncio

    class _MemFile:
        """[AsyncFileObject] を満たす最小の in-memory ストリーム（読み/書き両用の fake）。"""

        def __init__(self, data: bytes = b"") -> None:
            self._buf = bytearray(data)
            self._pos = 0

        async def read(self, size: int = -1) -> bytes:
            if size is None or size < 0:
                chunk = bytes(self._buf[self._pos :])
            else:
                chunk = bytes(self._buf[self._pos : self._pos + size])
            self._pos += len(chunk)
            return chunk

        async def write(self, data: bytes) -> int:
            self._buf += data
            return len(data)

        async def close(self) -> None:
            pass

        async def __aenter__(self) -> _MemFile:
            return self

        async def __aexit__(self, *exc: object) -> None:
            await self.close()

    async def main() -> None:
        cipher = XorStreamCipher(b"correct horse battery staple")
        plaintext = b"the quick brown fox jumps over the lazy dog" * 10

        # 暗号化書き込み（複数チャンクに分けて write ＝オフセット可変性を確認）。
        sink = _MemFile()
        async with CipherWriter(sink, cipher) as enc:
            for i in range(0, len(plaintext), 7):
                await enc.write(plaintext[i : i + 7])
        ciphertext = bytes(sink._buf)
        assert ciphertext != plaintext, "暗号文が平文と一致している（変換が効いていない）"
        assert len(ciphertext) == len(plaintext), "ストリーム暗号は長さを変えない"

        # 復号読み出し（書き込みとは別のチャンクサイズで read ＝境界非依存を確認）。
        source = _MemFile(ciphertext)
        out = bytearray()
        async with CipherReader(source, cipher) as dec:
            while True:
                chunk = await dec.read(13)
                if not chunk:
                    break
                out += chunk
        assert bytes(out) == plaintext, "round-trip 不一致（復号できていない）"

        # 一括 read でも一致（size=-1）。
        source2 = _MemFile(ciphertext)
        async with CipherReader(source2, cipher) as dec:
            assert await dec.read() == plaintext

        print("crypto self-test OK: round-trip & chunk-boundary independence")

    asyncio.run(main())


if __name__ == "__main__":
    _selftest()
