# Server Ops Kit for GitHub Copilot (v0.2 preview)

Linux サーバの構成把握、作業計画、限定された実行と記録を扱う Copilot Agent キット。VS Code の Copilot harness と、操作対象にアクセスできる管理端末で使う。クラウド Agent が社内ホストへ接続できることは前提にしない。

License: MIT。公開リポジトリに実ホストの台帳、作業記録、認証情報をコミットしない。`.gitignore` は誤追加防止の補助であり、秘密情報のアクセス制御を代替しない。

## ゴールと段階

1. `server-ops` → `server-inventory` が OS、パッケージ、稼働サービスを読み取り、`.server-ops/` に根拠・取得時刻付き台帳を保存する。
2. `server-ops` → `maintenance-planner` が要求を調査し、影響、前提、実行・検証・復旧条件を含む `plan.json` と人向け計画を作る。
3. `server-ops` → `maintenance-executor` が読み取り計画を `tools/run_plan.py` で実行し、各操作の終了コードと結果を保存する。追加対応は新しい計画に戻す。

現在実装する実行操作は `service-status`、`compose-status`、`compose-check` のみ。`compose-upgrade` と `--apply` は拒否する。構築、OS 更新、障害復旧、Compose 更新は調査・計画まで扱う。

## 利用開始

```sh
python3 -m unittest discover -s tests -v
python3 .github/skills/linux-inventory/scripts/collect.py --output .server-ops/hosts/host.json
```

Copilot で `server-ops` を選び、対象と目的を伝える。収集結果を確認してから計画を作る。計画の書式は `templates/plan.example.json` を参照。対象上で許可した計画を実行する例:

```sh
sha256sum .server-ops/plans/my-plan.json
python3 tools/run_plan.py --plan .server-ops/plans/my-plan.json --inventory .server-ops/hosts/host.json --confirm-sha <planのSHA256>
```

計画の SHA は**最終編集後**に計算する。`run_plan.py` はホスト名、台帳のハッシュ、取得範囲・完全性・鮮度を照合し、未知の操作・任意のシェル文字列を拒否する。SHA 照合は変更検出であり、人の承認を証明しない。Copilot の `execute` 権限自体は OS の権限境界ではない。管理権限や Docker ソケットへアクセスできない検証用アカウントでのみ使用する。読み取りの外部コマンドにも副作用や通信の可能性があり、各ツールの設定を事前に確認する。

実行記録は開始前から 0600 で保存し、各操作を `not_executed` / `running` / `completed` / `failed` / `result_unknown` に分ける。中断後の対象状態は `result_unknown` として現物を照合する。終了コード 0 でも外形・業務条件は `postconditions: not_assessed` のまま。更新の成功や本番運用可否を意味しない。

## 配置

キット全体（`.github/`、`tools/`、`templates/`、`tests/`、`README.md`、`AGENTS.md`、`.gitignore`）を Copilot の対象リポジトリへ配置する。管理ホストに Python 3.11 以降が必要。実行時データは `.server-ops/` に保存し Git へコミットしない。収集 CLI が終了 2 のときは部分取得なので、記録を確認し再収集する。コンテナ内の収集結果をホストの台帳として使わない。

定期レポートは `python3 tools/periodic_report.py --kind performance --output-dir .server-ops/reports` で送信せずに試す。セキュリティ報告も `--kind security` で試す。必要な配布元ツールとメタデータが使えなければ「未評価」で終了する。警告が出た場合も未評価とし、鮮度は別途確認する。送信する場合は `templates/report.example.json` を対象ホストの `/etc/server-ops-kit/report.json` に置いて宛先を設定し、専用アカウントにのみ読ませる。`templates/systemd/` のサービス・タイマーは `/opt/server-ops-kit`、レポート格納先 `/var/lib/server-ops-kit/reports`、ローカル時刻の 09:00/09:15 を前提にした**編集用ひな形**。対象ホストのパス・権限・タイムゾーン・SMTP リレーを確認してから導入する。事前に専用ユーザー `server-ops-reporter`、設定ディレクトリ、0700 の記録ディレクトリを用意し、コードは root 所有でレポーターが変更できないようにする。設定は root:server-ops-reporter の 0640 などで必要最小限に読ませ、`systemd-analyze verify` と送信先が無効なテスト環境で試す。メール送信は `--send` を指定したときだけ行い、記録の `delivery` は受信リレーまでの結果を示す。

## 前提、リスク、アンチゴール

- 構成収集は Ubuntu/Debian の dpkg と RHEL 系の rpm を識別する。未知の OS は詳細なパッケージ判定を保留する。
- `compose-update-manager` は別途管理ホストへ導入済みであること。更新を運用するなら、許可されたストレージ構成での手動・異常時復旧を使い捨て環境で確認する。キットからの自動適用と定期自動更新は停止中。
- 変更操作を再開するには、Copilot 実行アカウントから Docker ソケットと管理権限を分離し、別権限の承認者と保護された実行入口を設け、候補イメージ・既存状態・バックアップ・業務プローブ・再実行禁止を検証してから個別に受入試験を行う。現在は同梱していない。
- 台帳と作業記録には内部情報が含まれる。対象に置き、アクセス権を制限する。秘密情報を含む設定本文、`.env`、認証情報は収集しない。
- 任意コマンド実行、全ディストリ一括対応、OS の無人更新、Agent の常駐監視は対象外。
- 定期セキュリティレポートは OS パッケージの提供元が返すカバレッジ・アドバイザリの範囲。Ubuntu の `pro security-status` は全 CVE の自動評価と同義ではない。メタデータの鮮度も別途確認する。

## 次のフェーズ

1. 単一の検証用 Linux サーバで台帳の欠落・差分を確認する。
2. Compose の対象サービスで `check` → 使い捨て構成の更新・復旧 → 実サービスの計画へ進む。
3. `.github/skills/periodic-ops/` のレポートと timer を検証サーバで評価し、対象ごとの情報源・メール宛先・保存先を確定後に導入する。Skills は定期実行器ではない。

参照: [Copilot custom agents](https://docs.github.com/en/copilot/reference/custom-agents-configuration), [agent skills](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/add-skills), [Compose Update Manager](https://github.com/ardnico/compose-update-manager/blob/main/README.md)。
