# 要望: IPFS backend と ロードバランサーストレージ層（空定義＋ネタ）

（対話・着手前記録／意見すり合わせ済→scaffold 実装）

- **あるべき場所に空定義を置く**だけでよい（本体は未完成）。
- **IPFS**: 接続に必要な設定などのネタを用意。
- **ロードバランサー**: アルゴリズム決定用のネタを軽く用意。ArrayStorage の派生という見立て。

## すり合わせ結果（意見→確定）
- **IPFS**: KVS に素直に乗らない（CID 返り）。**MFS（/api/v0/files/*）を主**＝パス鍵・可変で KVS に乗る。
  CID 直アクセスは従（フックのみ）。両方の余地を残す。httpx 流用（新規重依存なし）。
  → **factory（backends/__init__.py）には載せない**（未完成のため。ユーザー指示）。
- **ロードバランサー**: シャーディング/レプリケーションではなく、**負荷メトリクス（CPU/メモリ/空き容量）を
  見て適切な1 backend を選ぶ**動的プレースメント型。ネタ＝①メトリクス capability `SupportsLoadStats`/
  `LoadStats`、②選択ポリシー `BalancePolicy`（RoundRobin/MostFreeSpace/LeastLoaded）。
  ArrayStorage の素直な派生ではなく**兄弟**（行き先が鍵に出ない／負荷で選ぶ）。読みルーティングの
  未解決論点（どの member に入れたか）は **probe-all を既定**として TODO 明記。facade 未公開。

## 成果（scaffold）
- `manystore/storage/backends/ipfs.py`（IpfsKeyValueStore/IpfsFileStore・本体 NotImplementedError）。
- `manystore/storage/surfaces/loadbalancer.py`（LoadStats/SupportsLoadStats/BalancePolicy＋3 policy stub＋
  LoadBalancedKeyValueStore・本体 NotImplementedError）。
