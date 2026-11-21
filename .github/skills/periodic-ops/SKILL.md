---
name: periodic-ops
description: Linux の脆弱性情報の定期確認と性能スナップショットのメール報告を systemd timer で設定・検証するときに使う。
---

# 定期レポート

`tools/periodic_report.py` と `templates/systemd/` を確認し、専用の権限制限された実行アカウントで試す。設定例は `templates/report.example.json`。認証情報を設定ファイルやリポジトリに置かない。メールは認証不要の承認済みローカルリレーを前提とし、送信先と通信経路を利用者と確定する。

- `--kind security`: Ubuntu は `pro security-status --format json`、RHEL 系は `dnf updateinfo list updates security`、Debian は導入済みの `debsecan` を利用する。ツール未導入・メタデータ未更新・ネットワーク不可なら「未評価」とし、脆弱性なしとは書かない。OS 以外のコンテナやアプリは別スコープ。
- `--kind performance`: load average、メモリ、ルート FS の使用量を採取。単発値と傾向を混同しない。性能の劣化判定には複数回の比較が必要。
- `--send` を付けなければメールしない。最初にレポートを見てから送信テストを行う。timer は対象環境のタイムゾーン、保守窓、宛先を確定してから有効化する。
- 定期 Docker 更新はこの Skill に混ぜない。既存 `svc-update.timer` とサービス単位の `auto_update` で管理する。

本スクリプトのセキュリティ出力はベンダーのカバレッジまたは利用可能なセキュリティ更新の状況であり、全 CVE の証明ではない。必要ならディストリ公式の OVAL/アドバイザリによる追加評価を計画する。
