"""exceptions — manystore の例外を 1 か所に集約し、HTTP Problem Details へ変換する。

散在していたドメイン例外（パス検証・context・multipart）をここへ集約する。基底 [ManystoreError] は
HTTP の **Problem Details**（RFC 9457・[PROBLEM_JSON]）に写せる `status`/`title` を持ち、
`to_problem()` で dict を返す。各ドメイン例外は**現行の stdlib 例外（KeyError / ValueError /
PermissionError）を
先頭に残したまま** [ManystoreError] を多重継承する＝`isinstance`/`str` は従来どおりで、既存の
`except`／HTTP 写像を壊さず problem 変換だけを足せる。

モジュール関数 [to_problem] は **任意の例外**（manystore 外の stdlib 例外も）を problem dict に写す
（[ManystoreError] は自身の status/title、その他は既定写像、未知は 500）。元モジュール
（stores.safe / implement.service / gateway.multipart）は後方互換で各例外を再エクスポートする。
"""

import io

# RFC 9457（旧 7807）Problem Details のメディアタイプ。
PROBLEM_JSON = "application/problem+json"


class ManystoreError(Exception):
    """manystore 例外の基底。HTTP の Problem Details へ写せる `status` / `title` を持つ。

    サブクラスは `status`（HTTP ステータス）と `title`（短い人間可読のラベル）を上書きする。
    `type` は問題種別を表す URI 参照（既定は RFC 9457 の `about:blank`）。
    """

    status: int = 500
    title: str = "Internal Server Error"
    type: str = "about:blank"

    def to_problem(self, *, instance: str | None = None) -> dict:
        """この例外を Problem Details(dict) に変換する（`application/problem+json` の本体）。

        `detail` は `str(self)`。`instance` は任意（その問題が起きた URI 参照を渡せる）。
        """
        problem: dict = {
            "type": self.type,
            "title": self.title,
            "status": self.status,
            "detail": str(self),
        }
        if instance is not None:
            problem["instance"] = instance
        return problem


class UnsafePathError(ValueError, ManystoreError):
    """安全でないキー/パスが渡された（絶対パス・`..`・NUL 等）。"""

    status = 400
    title = "Unsafe Path"


class ContextNotFound(KeyError, ManystoreError):
    """指定された context が公開されていない。"""

    status = 404
    title = "Context Not Found"


class ReadOnlyContext(PermissionError, ManystoreError):
    """書き込み不可（writable=false）の context に書き込もうとした。"""

    status = 403
    title = "Read-Only Context"


class NoSuchUpload(ManystoreError):
    """指定された uploadId が存在しない（未 create / abort 済み / complete 済み）。"""

    status = 404
    title = "No Such Upload"


class UnsupportedOperation(io.UnsupportedOperation, ManystoreError):
    """ストア/ストリームが対応しない操作（read-only backend への書き込み・reader への write 等）。

    stdlib の `io.UnsupportedOperation` を**先頭に残す**＝既存の `except io.UnsupportedOperation` や
    ファイルオブジェクトの慣習（reader に write したら拒否）を満たしつつ HTTP status を持たせる。
    backend/FileObject は生 `io.UnsupportedOperation` でなく**これ**を raise（例外は集約）。
    """

    status = 405
    title = "Method Not Allowed"


class ConflictError(ManystoreError):
    """並行更新の衝突（conditional put の条件不一致＝lost-update を fail-loud に拒否）。

    `put_if_absent`（既存あり）/ `put_if_match`（version 不一致）が満たせないとき上げる（M046）。
    """

    status = 409
    title = "Conflict"


# stdlib 例外 → (status, title) の既定写像。manystore 外の例外も problem にできる。
# サブクラス関係で取りこぼさないよう**具体的なものを先**に並べる（io.UnsupportedOperation は
# ValueError のサブクラスなので ValueError より前に置く）。
_STDLIB_PROBLEM: list[tuple[type, int, str]] = [
    (FileNotFoundError, 404, "Not Found"),
    (PermissionError, 403, "Forbidden"),
    (io.UnsupportedOperation, 405, "Method Not Allowed"),
    (ValueError, 400, "Bad Request"),
    (TimeoutError, 504, "Gateway Timeout"),
]


def to_problem(exc: Exception, *, instance: str | None = None) -> dict:
    """任意の例外を RFC 9457 Problem Details(dict) に変換する。

    [ManystoreError] は自身の `status`/`title`/`type` を使う。それ以外は stdlib の既定写像で
    `status`/`title` を当てる（未知は 500 / `about:blank`）。`detail` は `str(exc)`。
    返り値は `application/problem+json`（[PROBLEM_JSON]）でシリアライズすればよい。
    """
    if isinstance(exc, ManystoreError):
        return exc.to_problem(instance=instance)
    status, title = 500, "Internal Server Error"
    for typ, st, ti in _STDLIB_PROBLEM:
        if isinstance(exc, typ):
            status, title = st, ti
            break
    problem: dict = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "detail": str(exc),
    }
    if instance is not None:
        problem["instance"] = instance
    return problem
