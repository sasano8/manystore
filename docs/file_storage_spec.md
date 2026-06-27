# FileStore — conformance spec

> 自動生成: `make conformance-docs`（`python -m manystore.tools.conformancer`）。
> 手で編集しない。各実装が Protocol のメソッドを満たすか（メソッド存在チェック）を示す。
> ✅ = Implemented / ❌ = Not（`AsyncFileStore`）。

| メソッド | DictFileStore | LocalFileStore | S3FileStore | NatsFileStore | HttpFileStore |
|---|---|---|---|---|---|
| `aclose` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `connect` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `cp` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `create` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `delete` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `exists` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `get` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `get_or_raise` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `head` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `iter_all` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `list_all` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `mv` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `open_reader` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `open_writer` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `put` | ✅ | ✅ | ✅ | ✅ | ✅ |
