# Server Ops Kit for GitHub Copilot (v0.1)

Linux サーバの構成把握、作業計画、限定された実行と記録を扱う Copilot Agent キット。VS Code の Copilot harness と、操作対象にアクセスできる管理端末で使う。クラウド Agent が社内ホストへ接続できることは前提にしない。

License: MIT。公開リポジトリに実ホストの台帳、作業記録、認証情報をコミットしない。`.gitignore` は誤追加防止の補助であり、秘密情報のアクセス制御を代替しない。

## ゴールと段階

1. `server-ops` → `server-inventory` が OS、パッケージ、稼働サービスを読み取り、`.server-ops/` に根拠・取得時刻付き台帳を保存する。
2. `server-ops` → `maintenance-planner` が要求を調査し、影響、前提、実行・検証・復旧条件を含む `plan.json` と人向け計画を作る。
3. `server-ops` → `maintenance-executor` が確認された計画を `tools/run_plan.py` で実行し、各操作の終了コードと結果を保存する。追加対応は新しい計画に戻す。

現在実装する実行操作は `service-status`、`compose-status`、`compose-check`、`compose-upgrade` のみ。構築、OS 更新、障害復旧は調査・計画まで扱い、実行操作は個別に追加して検証する。

## 利用開始

```sh
python3 -m unittest discover -s tests -v
python3 .github/skills/linux-inventory/scripts/collect.py --output .server-ops/hosts/host.json
```

Copilot で `server-ops` を選び、対象と目的を伝える。収集結果を確認してから計画を作る。計画の書式は `templates/plan.example.json` を参照。対象上で許可した計画を実行する例:

```sh
sha256sum .server-ops/plans/my-plan.json
python3 tools/run_plan.py --plan .server-ops/plans/my-plan.json --inventory .server-ops/hosts/host.json --confirm-sha <planのSHA256>
# compose-upgrade を含む場合だけ、明示的に --apply も指定する
```

計画の SHA は**最終編集後**に計算する。`run_plan.py` はホスト名、台帳のハッシュ、台帳取得からの経過時間を照合し、未知の操作・任意のシェル文字列を拒否する。SHA 照合は変更検出であり、人の承認を証明しない。Copilot の `execute` 権限自体は OS の権限境界ではない。運用アカウントと sudo の権限を対象別に制限する。

## 配置

`.github/agents/` と `.github/skills/` を Copilot の対象リポジトリへ置く。`tools/`、`templates/`、`AGENTS.md` も同じリポジトリへ置く。管理ホストに Python 3.11 以降が必要。実行時データは `.server-ops/` に保存し Git へコミットしない。

定期レポートは `python3 tools/periodic_report.py --kind performance --output-dir .server-ops/reports` で送信せずに試す。セキュリティ報告も `--kind security` で試す。必要な配布元ツールとメタデータが使えなければ「未評価」で終了する。送信する場合は `templates/report.example.json` を対象ホストの `/etc/server-ops-kit/report.json` に置いて宛先を設定し、専用アカウントにのみ読ませる。`templates/systemd/` のサービス・タイマーは `/opt/server-ops-kit`、レポート格納先 `/var/lib/server-ops-kit/reports`、ローカル時刻の 09:00/09:15 を前提にした**編集用ひな形**。対象ホストのパス・権限・タイムゾーン・SMTP リレーを確認してから導入する。メール送信は `--send` を指定したときだけ行う。

## 前提、リスク、アンチゴール

- 構成収集は Ubuntu/Debian の dpkg と RHEL 系の rpm を識別する。未知の OS は詳細なパッケージ判定を保留する。
- `compose-update-manager` は別途管理ホストへ導入済みであること。自動更新の有効化や定期メールは対象ホストに実際の設定を導入する別作業とする。初版は定期レポートと timer のひな形を含む。
- `compose-upgrade` は既存 updater の `upgrade <登録名> --apply` を呼び出す。バックアップ・復旧の可否や特定サービスの互換性をキットが保証するものではない。
- 台帳と作業記録には内部情報が含まれる。対象に置き、アクセス権を制限する。秘密情報を含む設定本文、`.env`、認証情報は収集しない。
- 任意コマンド実行、全ディストリ一括対応、OS の無人更新、Agent の常駐監視は対象外。
- 定期セキュリティレポートは OS パッケージの提供元が返すカバレッジ・アドバイザリの範囲。Ubuntu の `pro security-status` は全 CVE の自動評価と同義ではない。メタデータの鮮度も別途確認する。

## 次のフェーズ

1. 単一の検証用 Linux サーバで台帳の欠落・差分を確認する。
2. Compose の対象サービスで `check` → 使い捨て構成の更新・復旧 → 実サービスの計画へ進む。
3. `.github/skills/periodic-ops/` のレポートと timer を検証サーバで評価し、対象ごとの情報源・メール宛先・保存先を確定後に導入する。Skills は定期実行器ではない。

参照: [Copilot custom agents](https://docs.github.com/en/copilot/reference/custom-agents-configuration), [agent skills](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/add-skills), [Compose Update Manager](https://github.com/ardnico/compose-update-manager/blob/main/README.md)。
