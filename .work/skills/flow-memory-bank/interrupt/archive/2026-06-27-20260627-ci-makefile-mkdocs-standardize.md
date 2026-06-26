---
from: dotfiles (supervisor)
type: instruction
priority: normal
date: 2026-06-27
---

# CI / Makefile / mkdocs を新標準に合わせる（下り dispatch）

supervisor 側で **CI とテスト Makefile の標準**を整備した（[[unit-quality]] R5/R13/R17 を更新、docs 公開は
新スキル **[[func-mkdocs]]** 新設）。manystore をこの標準に追従させてほしい。**急がない（normal）**——
manystore の `main` は壊れていない（PR #4 merge 済・main の Pages deploy 成功・サイト 200）。あくまで
**標準への追従**であって障害対応ではない。worker の flow 開発内ループで進めて構わない。

## 背景（PR で pages.yml が赤くなった件の正体）
失敗したのは PR run の `deploy` job が `github-pages` の**環境保護ルールで job-setup 落ち**（steps 0 個）。
現 `pages.yml` は `if: github.ref == 'refs/heads/main'` だけで、PR の `github.ref`（`refs/pull/N/merge`）依存。
→ **`event_name == 'push'` も AND** し、PR から到達する job に `environment:` を残さないのが恒久対策。

## やること（雛形の正本は上記スキル。差分だけ埋める）

1. **pages.yml のハードニング**（[[func-mkdocs]] の pages.yml 雛形に合わせる）
   - `deploy` job と `Upload Pages artifact` ステップの guard を
     **`if: github.event_name == 'push' && github.ref == 'refs/heads/main'`** に変更（`event_name` を追加）。
   - `environment: github-pages` は deploy job だけ・本番条件 guard 配下のまま（PR では踏まない）を確認。

2. **Makefile をテスト 4 段へ**（現状 `test`=not slow / `test-all`＝[[unit-quality]] R13 の新 4 段へ更新）
   - `test`=`-m "not slow and not benchmark"` / `test-heavy`=`-m "slow"` / `test-benchmark`=`-m "benchmark"` /
     `test-all`=全部。`.PHONY` と各ターゲットを 4 段に。`check` は `format-check test`（fast）のまま。
   - pyproject の `[tool.pytest.ini_options] markers` に **`benchmark`** を追加（`slow` は既存）。
     benchmark テストが無ければマーカー登録だけでよい（YAGNI＝空のまま test-benchmark は手動時に exit 5 でも可）。

3. **ci.yml の点検**（[[unit-quality]] R17）
   - 既に `make check` を push/PR で回しており要件は満たす。**Node20 廃止予告の warning**（`actions/checkout` /
     `actions/upload-artifact` / `astral-sh/setup-uv@v5` が Node24 強制）が出ているので、各 action を新しめの
     major へ更新（例 `setup-uv@v6`）して warning を消す。機能影響はない軽微対応。
   - 重い層（`test-heavy`）を CI で回すなら docker compose（既存の `e2e-up`）でサービスを立てる別 job/別ステップに。
     `test-benchmark` は環境差で揺れるので gate にせず情報収集に留める。

4. **mkdocs は概ね追従済み**（`mkdocs.yml`/`docs/`/`make docs`/`docs-serve`/`docs` 依存あり）。[[func-mkdocs]] の
   雛形と照らし、`mkdocs build --strict` 緑・`docs:` の生成前段（conformance 再生成）が壊れていないかだけ確認。

## 完了条件
- `make check` 緑、`pages.yml` の PR run が deploy を skip して緑、`make test-heavy`/`test-benchmark`/`test-all`
  が（該当が無くても）標準形で叩ける。flow の開発内ループに沿って 1 コミットで。
