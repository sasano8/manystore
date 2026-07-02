"""構成ファイルからストア復元（M070）のテスト。

local 相対パスの構成ファイルのディレクトリ基準での解決・上方向 discovery・`open_store` の名前解決・
`manystore store init` CLI を検証する。
"""

from pathlib import Path

import pytest

from manystore import (
    discover_store_config,
    find_config_file,
    load_store_config,
    open_store,
)
from manystore.__main__ import main as cli_main
from manystore.storage.config import CONFIG_FILENAME, parse_store_config


def test_local_root_resolved_relative_to_config_dir(tmp_path: Path) -> None:
    # local の相対 root は base_dir（構成ファイルのディレクトリ）基準で絶対化される（cwd 非依存）。
    base = tmp_path / "proj"
    base.mkdir()
    cfg = parse_store_config(
        {"contexts": {"data": {"backend": "local", "root": "store"}}}, base_dir=base
    )
    assert cfg.contexts["data"].opts["local_dir"] == base / "store"


def test_local_absolute_root_kept(tmp_path: Path) -> None:
    cfg = parse_store_config(
        {"contexts": {"d": {"backend": "local", "root": str(tmp_path)}}}, base_dir=Path("/other")
    )
    assert cfg.contexts["d"].opts["local_dir"] == tmp_path  # 絶対 root は base_dir 無視


def test_load_store_config_uses_file_dir_as_base(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(
        'default_context = "d"\n[contexts.d]\nbackend = "local"\nroot = "sub"\n', encoding="utf-8"
    )
    cfg = load_store_config(tmp_path / CONFIG_FILENAME)
    assert cfg.default_context == "d"
    assert cfg.contexts["d"].opts["local_dir"] == tmp_path / "sub"


def test_find_config_walks_upward(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text('[contexts.x]\nbackend = "memory"\n', encoding="utf-8")
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    assert find_config_file(deep) == tmp_path / CONFIG_FILENAME
    # 見つからないケース。
    assert find_config_file(tmp_path.parent / "nowhere-xyz") is None


def test_discover_returns_none_when_absent(tmp_path: Path) -> None:
    assert discover_store_config(tmp_path) is None


async def test_open_store_resolves_context_name(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(
        'default_context = "data"\n[contexts.data]\nbackend = "local"\nroot = "store"\n',
        encoding="utf-8",
    )
    cfg = load_store_config(tmp_path / CONFIG_FILENAME)
    # 名前解決（明示 config 渡し）で local(base_dir/store) に put/get できる。
    async with open_store("data", config=cfg) as store:
        await store.put("k", b"v")
        assert await store.get("k") == b"v"
    assert (tmp_path / "store" / "k").read_bytes() == b"v"  # 構成ディレクトリ基準で作られた


async def test_open_store_empty_name_uses_default_context(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(
        'default_context = "d"\n[contexts.d]\nbackend = "memory"\n', encoding="utf-8"
    )
    cfg = load_store_config(tmp_path / CONFIG_FILENAME)
    async with open_store("", config=cfg) as store:
        await store.put("k", b"v")
        assert await store.get("k") == b"v"


async def test_open_store_unknown_context_raises(tmp_path: Path) -> None:
    cfg = load_store_config(
        _write(tmp_path, 'default_context = "d"\n[contexts.d]\nbackend = "memory"\n')
    )
    with pytest.raises(ValueError, match="unknown context"):
        async with open_store("nope", config=cfg):
            pass


def test_open_store_no_config_found_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 構成ファイルが無いディレクトリで scheme 無しの名前解決＝discovery→None→ValueError（即時）。
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="見つからない"):
        open_store("ctx")  # _resolve_context は open_store 呼び出し時に即時実行される


def test_cli_store_init_creates_config(tmp_path: Path) -> None:
    cli_main(["store", "init", str(tmp_path)])
    p = tmp_path / CONFIG_FILENAME
    assert p.is_file()
    cfg = load_store_config(p)
    assert "default" in cfg.contexts and cfg.default_context == "default"
    # 既存があると --force 無しは失敗。
    with pytest.raises(SystemExit):
        cli_main(["store", "init", str(tmp_path)])
    cli_main(["store", "init", str(tmp_path), "--force"])  # --force で上書き OK


def test_cli_backcompat_config_routes_to_serve(tmp_path: Path) -> None:
    # 旧 `manystore --config X`（先頭 --config）は serve に振られる＝load_config へ到達する。
    # 存在しない config を渡すと uvicorn 起動前に FileNotFoundError＝振り分けが効いている証拠。
    with pytest.raises(FileNotFoundError):
        cli_main(["--config", str(tmp_path / "nope.toml")])


def _write(d: Path, body: str) -> Path:
    p = d / CONFIG_FILENAME
    p.write_text(body, encoding="utf-8")
    return p
