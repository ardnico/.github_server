---
name: server-topology
description: Linux サーバの台帳と確認済みの関係情報から Agent 向け構成 JSON と人向け Mermaid 構成図を作る段階 1 で使う。
---

# 根拠付きサーバ構成

1. `linux-inventory` で取得した完全なローカルホスト台帳を使用する。対象が別ホスト・コンテナなら停止する。`tools/build_topology.py` はネットワーク探索やリモート接続をしない。
2. 台帳の稼働サービス名からは、そのサービスが台帳取得時にローカルホストで稼働していた関係だけを生成する。依存、通信、保存先、外部到達性を推測で結ばない。
3. 追加の関係は `templates/topology-claims.example.json` の形式で、利用者の確認または限定的な設定観測を根拠として登録する。各 node/edge に `kind`、`source_ref`、タイムゾーン付き `observed_at` が必要。仮説は `hypothesis` と明記する。ソースの本文や秘密情報を入れない。
4. 必要な関係が不足すれば主担当に質問項目を返す。利用者から回答がないまま `user-confirmed` にしない。技術的に収集できない構成も未確認とする。
5. `python3 tools/build_topology.py --inventory .server-ops/hosts/<id>.json --output-prefix .server-ops/topology/<id>` で `.json` と `.md` を作る。追加情報がある場合だけ `--claims .server-ops/topology/<id>-claims.json` を指定する。出力と claims は Git 除外、専用ユーザーの権限内に置く。構成図は時点の観測であり、今も稼働中という保証はない。
