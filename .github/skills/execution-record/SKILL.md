---
name: execution-record
description: 読み取り計画を限定実行し、操作、終了コード、未検証条件、追加対応を記録するときに使う。
---

# 実行記録

`tools/run_plan.py` は最終計画の SHA、台帳 SHA、ホスト名、台帳の完全性・鮮度を照合する。変更操作 `compose-upgrade` と `--apply` は現在拒否する。計画にない操作は実行しない。SHA は承認の証明ではない。

JSON 記録は実行端末の `.server-ops/runs/` に保存。操作別の `completed`、`failed`、`not_executed`、`result_unknown` を区別し、`postconditions: not_assessed` を別途検証する。中断時は現物の状態を確認し、新しい計画へ戻す。失敗後に独断で続行しない。

診断ログに秘密情報が混ざる危険がある。スクリプトは既知パターンを伏せ、記録ファイルを 0600 で作るが、すべての漏洩を防ぐ保証はない。記録は公開リポジトリやメールに添付しない。
