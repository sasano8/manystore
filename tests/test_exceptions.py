"""exceptions の集約と Problem Details 変換のテスト。

(1) ドメイン例外が `manystore.exceptions` に集約され、元モジュールからも後方互換で import できる、
(2) stdlib 基底（KeyError/ValueError/PermissionError）の `isinstance`/`str` が従来どおり、
(3) `to_problem` が ManystoreError / stdlib 例外を RFC 9457 Problem Details(dict) に写す、を確認。
"""

import io

import manystore
from manystore.exceptions import (
    PROBLEM_JSON,
    ContextNotFound,
    ManystoreError,
    NoSuchUpload,
    ReadOnlyContext,
    UnsafePathError,
    to_problem,
)


def test_problem_json_media_type() -> None:
    assert PROBLEM_JSON == "application/problem+json"


def test_domain_exceptions_keep_stdlib_bases() -> None:
    # 多重継承で ManystoreError 性を足しつつ、従来の stdlib 例外でもある（後方互換）。
    assert issubclass(UnsafePathError, (ValueError, ManystoreError))
    assert issubclass(ContextNotFound, (KeyError, ManystoreError))
    assert issubclass(ReadOnlyContext, (PermissionError, ManystoreError))
    assert issubclass(NoSuchUpload, ManystoreError)
    # KeyError 由来の str（クォート付き）も維持される。
    assert str(ContextNotFound("work")) == "'work'"


def test_reexport_from_original_modules() -> None:
    # 集約しても元モジュール（safe_path / service / multipart）から import できる＝同一クラス。
    from manystore.serving.gateway.multipart import NoSuchUpload as MpNoSuchUpload
    from manystore.serving.services.service import ContextNotFound as SvcCtx
    from manystore.serving.services.service import ReadOnlyContext as SvcRo
    from manystore.storage.surfaces.safe import UnsafePathError as SpUnsafe

    assert SpUnsafe is UnsafePathError
    assert SvcCtx is ContextNotFound
    assert SvcRo is ReadOnlyContext
    assert MpNoSuchUpload is NoSuchUpload
    # トップレベルからも公開。
    assert manystore.ManystoreError is ManystoreError
    assert manystore.to_problem is to_problem


def test_to_problem_for_manystore_error() -> None:
    problem = to_problem(ContextNotFound("work"), instance="/contexts/work")
    assert problem["status"] == 404
    assert problem["title"] == "Context Not Found"
    assert problem["type"] == "about:blank"
    assert problem["instance"] == "/contexts/work"
    assert "work" in problem["detail"]
    # instance を渡さなければキーが無い。
    assert "instance" not in to_problem(ReadOnlyContext("ro"))


def test_to_problem_status_per_subclass() -> None:
    assert to_problem(UnsafePathError("../x"))["status"] == 400
    assert to_problem(ReadOnlyContext("ro"))["status"] == 403
    assert to_problem(NoSuchUpload("abc"))["status"] == 404


def test_to_problem_maps_stdlib_exceptions() -> None:
    # manystore 外の stdlib 例外も既定写像で problem にできる。
    assert to_problem(FileNotFoundError("k"))["status"] == 404
    assert to_problem(PermissionError("x"))["status"] == 403
    # io.UnsupportedOperation は ValueError のサブクラスだが 405 に正しく当たる（順序）。
    assert to_problem(io.UnsupportedOperation("nope"))["status"] == 405
    assert to_problem(ValueError("bad"))["status"] == 400
    assert to_problem(TimeoutError("late"))["status"] == 504
    # 未知の例外は 500。
    p = to_problem(RuntimeError("boom"))
    assert p["status"] == 500 and p["title"] == "Internal Server Error"
    assert p["detail"] == "boom"
