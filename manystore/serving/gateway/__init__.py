"""gateway — manystore を S3 互換 API として公開するフロント層（M021 S1）。

既存の [StorageService]（implement 層）の上に「S3 protocol」を薄く乗せるだけで、コア IF
（KeyValueStore / FileStore）は不変。fastapi/uvicorn は遅延 import（`manystore[server]`
extra を流用＝新依存ゼロ）。S3 XML は stdlib `xml.etree.ElementTree` で生成する。

S1 で対応する操作: GetObject / PutObject / HeadObject / DeleteObject / ListObjectsV2。
S2 で追加: Multipart Upload（Create/UploadPart/Complete/Abort・[multipart] 参照）。
S3 passthrough・継続トークンページング・ListParts/ListMultipartUploads はバックログ
（progress.md M021）。
"""

from .app import create_gateway

__all__ = ["create_gateway"]
