# KeyValueStore — conformance spec

> 自動生成: `make conformance-docs`（`python -m manystore.spec.conformancer`）。
> 手で編集しない。各実装が Protocol のメソッドを満たすか（メソッド存在チェック）を示す。
> ✅ = Implemented / ❌ = Not（`AsyncBufferedStore`）。

| メソッド | DictStore | PosixLocalStore | S3Store | NatsStore | HttpStore | RemoteStore |
|---|---|---|---|---|---|---|
| `aclose` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `connect` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `cp` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `create` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `delete` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `exists` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `get` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `get_or_raise` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `head` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `head_or_absent` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `iter_all` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `list_all` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `mv` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `put` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
