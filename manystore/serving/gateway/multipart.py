"""multipart — S3 Multipart Upload を共通 IF の上に薄く実現する（M021 S2）。

S3 の multipart upload（CreateMultipartUpload / UploadPart / CompleteMultipartUpload /
AbortMultipartUpload）を、**コア IF を一切変えず** [StorageService] の put/get/delete/
list_entries だけで実現する。

## 状態の保持方式（一時キー空間）

multipart の途中状態（uploadId → 各 part のバイト）を **ストア上の予約プレフィクス**に置く:

    {RESERVED_PREFIX}/{upload_id}/{part_number:05d}

インメモリ辞書ではなく **ストア上の一時キー空間**を採るのは指示どおり:
- **サーバ再起動耐性**: プロセスが落ちても part は残る（同一 uploadId で再 complete 可能）。
- **複数プロセス/ワーカ耐性**: どのワーカが UploadPart を受けても同じストアに積まれる。

予約プレフィクスは `validate_safe_path` を通る安全なキー（先頭 '/' 無し・'..' 無し・
バックスラッシュ無し）。通常オブジェクトと衝突しないよう先頭ドットの予約名前空間にする。
**ListObjectsV2 はこのプレフィクスを除外**（gateway 側でフィルタ）＝一時 part は見えない。

## 並行 UploadPart / 上書き / 順序

- **part の上書き**: 同一 (uploadId, partNumber) への UploadPart は、その part キーへの
  put で**最後の書き込みが勝つ**（S3 と同じ last-writer-wins）。put は backend 既定の
  アトミック書き込み（local=temp+rename）なので、並行しても part が**半端に混ざらない**。
- **part の順序**: 結合順は **CompleteMultipartUpload のリクエスト本文に並んだ partNumber 順**
  に従う（クライアント責務。サーバは再ソートしない）。partNumber はキーにゼロ詰めで埋めるが、
  結合順そのものはリクエスト指定順を尊重する。
- **all-or-nothing**: complete は全 part を読み出して結合し、本オブジェクトへ **1 回の put**
  で書く（put 自体がアトミック）。書き込み成功後に一時 part を掃除する。

## ETag

S3 の multipart ETag 規約に合わせる: 各 part の MD5（バイナリ）を連結した MD5 の hex に
`-{partCount}` を付けて二重引用符で囲む（例 `"<md5hex>-3"`）。
"""

import hashlib
import uuid

from ...spec.exceptions import ContextNotFound, NoSuchUpload  # 集約先（後方互換で再エクスポート）
from ..services.service import StorageService

# 一時 part を置く予約プレフィクス（通常オブジェクトと衝突しない先頭ドット名前空間）。
RESERVED_PREFIX = ".manystore-mpu"


def is_reserved_key(key: str) -> bool:
    """ListObjectsV2 で隠すべき multipart 一時キーかどうか。"""
    return key == RESERVED_PREFIX or key.startswith(RESERVED_PREFIX + "/")


def _part_key(upload_id: str, part_number: int) -> str:
    """uploadId 名前空間内の part キー。partNumber はゼロ詰めで列挙安定にする。"""
    return f"{RESERVED_PREFIX}/{upload_id}/{part_number:05d}"


def _upload_prefix(upload_id: str) -> str:
    return f"{RESERVED_PREFIX}/{upload_id}/"


def new_upload_id() -> str:
    """衝突しない uploadId を発行する（uuid4 hex）。"""
    return uuid.uuid4().hex


async def create_upload(service: StorageService, bucket: str) -> str:
    """CreateMultipartUpload: uploadId を発行する。

    この時点ではマーカは置かない（part が 0 件でも create は成立する＝S3 と同じ）。
    書き込み可否は最初の UploadPart / Complete の put で検証される。bucket（context）の
    存在だけは早期に確かめておく（存在しない bucket への create は NoSuchBucket）。
    """
    if not any(c.name == bucket for c in service.list_contexts()):
        raise ContextNotFound(bucket)
    return new_upload_id()


async def upload_part(
    service: StorageService, bucket: str, upload_id: str, part_number: int, data: bytes
) -> str:
    """UploadPart: 1 part をストア上の一時キーへ put し、その ETag（part の MD5）を返す。

    同一 (uploadId, partNumber) への再 put は last-writer-wins（S3 互換）。
    """
    await service.put(bucket, _part_key(upload_id, part_number), data)
    return '"' + hashlib.md5(data).hexdigest() + '"'  # noqa: S324  (ETag 用途)


async def complete_upload(
    service: StorageService, bucket: str, key: str, upload_id: str, part_numbers: list[int]
) -> str:
    """CompleteMultipartUpload: 指定順に part を結合して本オブジェクトへ書き、ETag を返す。

    - `part_numbers` はクライアントが指定した結合順（昇順想定だがサーバは尊重するだけ）。
    - 指定された part が 1 つでも欠けていれば [NoSuchUpload] 相当のエラー（呼び出し側が
      InvalidPart 等へマップ）。結合後は本オブジェクトへ **1 回の put**（all-or-nothing）し、
      成功後に一時 part を掃除する。
    """
    if not part_numbers:
        raise NoSuchUpload(upload_id)
    chunks: list[bytes] = []
    md5_concat = hashlib.md5()  # noqa: S324
    for pn in part_numbers:
        part = await service.get(bucket, _part_key(upload_id, pn))
        if part is None:
            raise NoSuchUpload(f"{upload_id}: missing part {pn}")
        chunks.append(part)
        md5_concat.update(hashlib.md5(part).digest())  # noqa: S324
    # all-or-nothing で本オブジェクトへ書く（put 自体がアトミック）。
    await service.put(bucket, key, b"".join(chunks))
    await _cleanup(service, bucket, upload_id)
    return f'"{md5_concat.hexdigest()}-{len(part_numbers)}"'


async def abort_upload(service: StorageService, bucket: str, upload_id: str) -> None:
    """AbortMultipartUpload: uploadId 配下の一時 part を全削除する（冪等）。"""
    await _cleanup(service, bucket, upload_id)


async def _cleanup(service: StorageService, bucket: str, upload_id: str) -> None:
    """uploadId 配下の一時 part をすべて削除する。"""
    prefix = _upload_prefix(upload_id)
    # 多めに取って全 part を掃除（part 数は max-keys 既定 1000 を超え得るので大きめ上限）。
    entries = await service.list_entries(bucket, prefix=prefix, limit=1_000_000)
    for e in entries:
        await service.delete(bucket, e.key)
