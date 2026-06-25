# 要望: conformancer 結果を docs に出力する Makefile ターゲット（M034）

- ユーザー要望（2026-06-25）: conformancer の結果を docs に出力するのを Makefile に追加してほしい。
- 該当バックログ: **M034**（conformance 結果を docs に spec 表出力＋Makefile キック）。
- 方針: conformancer に CLI 入口（`python -m manystore.tools.conformancer`）を新設し、各実装の
  メソッド × Implemented/Not を `docs/file_storage_spec.md` / `docs/kv_spec.md` に出力。Makefile に
  キックターゲットを追加。接続不要のメソッド存在チェックを正本にする（実 backend 不要で決定的）。
