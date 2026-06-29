"""exceptions — manystore の例外を 1 か所に集約し、HTTP Problem Details へ変換する。

散在していたドメイン例外（パス検証・context・multipart）をここへ集約する。基底 [ManystoreError] は
HTTP の **Problem Details**（RFC 9457・[PROBLEM_JSON]）に写せる `status`/`title` を持ち、
`to_problem()` で dict を返す。各ドメイン例外は**現行の stdlib 例外（KeyError / ValueError /
PermissionError）を
先頭に残したまま** [ManystoreError] を多重継承する＝`isinstance`/`str` は従来どおりで、既存の
`except`／HTTP 写像を壊さず problem 変換だけを足せる。

**任意の例外**（manystore 外の stdlib も）を problem dict に写すロジックは **基底メソッド
[ManystoreError.problem_for]**（classmethod）が正本（[ManystoreError] は自身の status/title、他は
既定写像 [_STDLIB_PROBLEM]、未知は 500）。関数 [to_problem] はそこへの薄い委譲（後方互換）。
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
        return self._problem_dict(self.type, self.title, self.status, str(self), instance)

    @classmethod
    def problem_for(cls, exc: Exception, *, instance: str | None = None) -> dict:
        """**任意の例外**を RFC 9457 Problem Details(dict) に写す（変換ロジックの正本）。

        [ManystoreError] は自身の `status`/`title`/`type`（`to_problem`）を使う。他は stdlib の
        既定写像 [_STDLIB_PROBLEM] で当てる（未知は 500 / `about:blank`）。`detail` は `str(exc)`。
        返り値は `application/problem+json`（[PROBLEM_JSON]）でシリアライズする。
        """
        if isinstance(exc, ManystoreError):
            return exc.to_problem(instance=instance)
        status, title = 500, "Internal Server Error"
        for typ, st, ti in _STDLIB_PROBLEM:
            if isinstance(exc, typ):
                status, title = st, ti
                break
        return cls._problem_dict("about:blank", title, status, str(exc), instance)

    @staticmethod
    def _problem_dict(
        type_: str, title: str, status: int, detail: str, instance: str | None
    ) -> dict:
        """Problem Details(dict) を組む共通ヘルパ（`instance` は与えられたときだけ載せる）。"""
        problem: dict = {"type": type_, "title": title, "status": status, "detail": detail}
        if instance is not None:
            problem["instance"] = instance
        return problem


class UnsafePathError(ValueError, ManystoreError):
    """安全でないキー/パスが渡された（絶対パス・`..`・NUL 等）。"""

    status = 400
    title = "Unsafe Path"


class NotFoundError(FileNotFoundError, ManystoreError):
    """キー/ファイルが存在しない（`get_or_raise` 等の欠損正規化先）。

    stdlib の `FileNotFoundError` を**先頭に残す**＝既存の `except FileNotFoundError` や
    `pytest.raises(FileNotFoundError)` を継承で満たしつつ、manystore 例外ファミリ（status/title・
    problem 変換）に載せる。backend は欠損を生 `FileNotFoundError` でなく**これ**で正規化する。
    """

    status = 404
    title = "Not Found"


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


class IntegrityError(ManystoreError):
    """取得データが期待メタと一致しない（download の整合性検証で size/hash 不一致）。

    truncation・転送破損・キャッシュ汚染などを fail-loud に拒否する（M067・`Verify` 参照）。
    """

    status = 422
    title = "Integrity Error"


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
    """任意の例外を RFC 9457 Problem Details(dict) に変換する（後方互換の関数 API）。

    変換ロジックの**正本は基底メソッド [ManystoreError.problem_for]**。本関数はそこへの薄い委譲
    （既存の `from .exceptions import to_problem` 呼び出しをそのまま保つ）。
    """
    return ManystoreError.problem_for(exc, instance=instance)
