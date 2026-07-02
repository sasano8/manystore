# FileStore — conformance spec

> 自動生成: `make conformance-docs`（`python -m manystore.spec.conformancer`）。
> 手で編集しない。各実装が Protocol のメソッドを満たすか（メソッド存在チェック）を示す。
> ✅ = Implemented / ❌ = Not（`AsyncStore`）。

| メソッド | DictStore | PosixLocalStore | S3Store | NatsStore | HttpStore |
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
| `head_or_absent` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `iter_all` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `list_all` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `mv` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `open_reader` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `open_writer` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `put` | ✅ | ✅ | ✅ | ✅ | ✅ |
