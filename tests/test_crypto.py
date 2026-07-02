"""crypto（ストリーム暗号と FileStore IO 繋ぎこみ）のテスト。

crypto.py のインライン `_selftest`（`__main__` でしか走らず CI 収集対象外だった）を pytest へ移し、
契約を CI で常時検証する（M060）:
(1) XorStreamCipher の round-trip と **チャンク境界非依存**（どう分割しても結合結果が一致）、
(2) 暗号文は平文と異なり長さは不変、XOR は encrypt==decrypt（対称）、
(3) CipherWriter/CipherReader 越しの round-trip と read/write の方向別拒否（UnsupportedOperation）、
(4) 空鍵 ValueError。
"""

import pytest

from manystore.crypto import (
    CipherReader,
    CipherWriter,
    StreamCipher,
    XorStreamCipher,
)
from manystore.spec.exceptions import UnsupportedOperation


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


PLAINTEXT = b"the quick brown fox jumps over the lazy dog" * 10
KEY = b"correct horse battery staple"


def test_xor_cipher_implements_stream_cipher() -> None:
    assert isinstance(XorStreamCipher(KEY), StreamCipher)


def test_xor_cipher_empty_key_rejected() -> None:
    with pytest.raises(ValueError):
        XorStreamCipher(b"")


def test_xor_cipher_is_symmetric() -> None:
    # XOR キーストリームは encrypt == decrypt（同じ transform で復号できる）。
    cipher = XorStreamCipher(KEY)
    ct = cipher.transform(0, PLAINTEXT)
    assert ct != PLAINTEXT
    assert len(ct) == len(PLAINTEXT)  # ストリーム暗号は長さを変えない。
    assert cipher.transform(0, ct) == PLAINTEXT


def test_xor_cipher_chunk_boundary_independent() -> None:
    # 同じバイト列なら、どんなチャンク境界で transform を呼んでも結合結果は一致する。
    cipher = XorStreamCipher(KEY)
    whole = cipher.transform(0, PLAINTEXT)

    pieced = bytearray()
    offset = 0
    for step in (1, 7, 13, 64, 256):
        chunk = PLAINTEXT[offset : offset + step]
        if not chunk:
            break
        pieced += cipher.transform(offset, chunk)
        offset += len(chunk)
    pieced += cipher.transform(offset, PLAINTEXT[offset:])
    assert bytes(pieced) == whole


async def test_cipher_writer_reader_round_trip() -> None:
    cipher = XorStreamCipher(KEY)

    # 暗号化書き込み（小さなチャンクに分割＝オフセット可変性）。
    sink = _MemFile()
    async with CipherWriter(sink, cipher) as enc:
        for i in range(0, len(PLAINTEXT), 7):
            await enc.write(PLAINTEXT[i : i + 7])
    ciphertext = bytes(sink._buf)
    assert ciphertext != PLAINTEXT
    assert len(ciphertext) == len(PLAINTEXT)

    # 復号読み出し（書き込みとは別のチャンクサイズ＝境界非依存）。
    source = _MemFile(ciphertext)
    out = bytearray()
    async with CipherReader(source, cipher) as dec:
        while True:
            chunk = await dec.read(13)
            if not chunk:
                break
            out += chunk
    assert bytes(out) == PLAINTEXT


async def test_cipher_reader_one_shot_read() -> None:
    cipher = XorStreamCipher(KEY)
    ciphertext = cipher.transform(0, PLAINTEXT)
    source = _MemFile(ciphertext)
    async with CipherReader(source, cipher) as dec:
        assert await dec.read() == PLAINTEXT


async def test_cipher_reader_rejects_write() -> None:
    dec = CipherReader(_MemFile(), XorStreamCipher(KEY))
    with pytest.raises(UnsupportedOperation):
        await dec.write(b"x")


async def test_cipher_writer_rejects_read() -> None:
    enc = CipherWriter(_MemFile(), XorStreamCipher(KEY))
    with pytest.raises(UnsupportedOperation):
        await enc.read()


async def test_cipher_writer_closes_inner() -> None:
    # ラッパの close が下層へ委譲されること（リーク防止の最小契約）。
    closed = {"v": False}

    class _Spy(_MemFile):
        async def close(self) -> None:
            closed["v"] = True

    async with CipherWriter(_Spy(), XorStreamCipher(KEY)) as enc:
        await enc.write(b"hello")
    assert closed["v"] is True
