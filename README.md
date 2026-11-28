# Server Ops Kit for Copilot / Codex / Claude Code (v0.2 preview)

Linux サーバの構成把握、作業計画、限定された実行と記録を扱う Agent キット。Copilot、Codex、Claude Code と、操作対象にアクセスできる管理端末で使う。クラウド Agent が社内ホストへ接続できることは前提にしない。

License: MIT。公開リポジトリに実ホストの台帳、作業記録、認証情報をコミットしない。`.gitignore` は誤追加防止の補助であり、秘密情報のアクセス制御を代替しない。

## ゴールと段階

1. `server-ops` → `server-inventory` が OS、パッケージ、稼働サービスを読み取り、`.server-ops/` に根拠・取得時刻付き台帳を保存する。必要なら構成 JSON と人向け図も作る。
2. `server-ops` → `maintenance-planner` が要求を調査し、影響、前提、実行・検証・復旧条件を含む `plan.json` と人向け計画を作る。
3. `server-ops` → `maintenance-executor` が読み取り計画を `tools/run_plan.py` で実行し、各操作の終了コードと結果を保存する。追加対応は新しい計画に戻す。

現在実装する実行操作は `service-status`、`compose-status`、`compose-check` のみ。後者2つは管理者が別途発行した限定スナップショットを非特権で読む。`compose-upgrade` と `--apply` は拒否する。構築、OS 更新、障害復旧、Compose 更新は調査・計画まで扱う。

## 配置後のファイル構成

以下は Ubuntu の検証 VM `lab-ops-01` に、Agent を `kit-reader` で動かす例。リポジトリを開く作業領域と、root が systemd から実行するコードを分ける。`kit-reader` に Docker・sudo 権限は与えない。

| 配置先 | ファイル・ディレクトリ | 所有者と用途 |
| --- | --- | --- |
| `/home/kit-reader/server-ops-workspace/` | `README.md`、`AGENTS.md`、`.gitignore`、`LICENSE`、`tests/` | `kit-reader` が Agent から開くリポジトリ。テストと運用指示を参照する。 |
| 同上 | `.github/agents/*.agent.md`、`.github/skills/*/SKILL.md` と `linux-inventory/scripts/collect.py` | Copilot の Agent 4件、Skill 10件と台帳収集スクリプト（正本）。 |
| 同上 | `AGENTS.md`、`CLAUDE.md`、`.codex/agents/*.toml`、`.claude/agents/*.md`、`.agents/skills/*/SKILL.md`、`.claude/skills/*/SKILL.md` | Codex と Claude Code の入口、役割、Skill のアダプター。Skill 本文の正本は `.github/skills/`。 |
| 同上 | `tools/run_plan.py`、`tools/build_topology.py`、`tools/compose_snapshot.py`、`tools/periodic_report.py`、`templates/` | 非特権の読み取り・記録・構成図生成ツールと計画・systemd ひな形。作業リポジトリのコードを root で実行しない。 |
| 同上 | `.server-ops/hosts/`、`.server-ops/topology/`、`.server-ops/plans/`、`.server-ops/runs/`、`.server-ops/reports/` | `kit-reader` の実行時データ。Git の追跡対象外。台帳、構成図、計画、実行記録は公開しない。 |
| `/opt/server-ops-kit/tools/` | `compose_snapshot.py`、`periodic_report.py` | 管理者が検査済みの版を別に配置し、root 所有・`kit-reader` 書込不可とする。root の発行 unit はこのコピーだけを実行。 |
| `/etc/systemd/system/` | `server-ops-compose-snapshot.{service,timer}`、必要時のみ `server-ops-{security,performance}.{service,timer}` | root が設置・管理する unit。パス・権限を確認してから有効化。 |
| `/etc/svc-update/config.toml`、`/var/backups/svc-update/` | updater の設定とバックアップ・ロック | root 専用。`kit-reader` には公開しない。 |
| `/var/lib/server-ops-kit/compose-state/snapshot.json` | 限定 Compose スナップショット | root が発行。root:server-ops-reader 0640、親ディレクトリは root:server-ops-reader 0750。 |
| `/etc/server-ops-kit/report.json`、`/var/lib/server-ops-kit/reports/` | SMTP 設定と定期レポート | レポート機能を使う場合だけ設置。レポート用アカウントの権限は後述。 |

作業リポジトリに `.github/` と `tools/` が必要。systemd 用の root 所有コピーは作業リポジトリの代わりにならない。root の unit が作業リポジトリのスクリプトを参照すると、Agent の編集内容を特権で実行する構成になるため、`ExecStart` は `/opt/server-ops-kit/tools/` を指す。

## 3段階の具体例

仮に `lab-ops-01` の Compose 登録名 `web` について「次の更新候補を調べたい」と依頼した場合:

| 段階 | 必要な入力と成果物 | Agent が確認する例 |
| --- | --- | --- |
| 1. 環境登録 | 対象が VS Code を開いているホストか、サービス `web` の役割。`.server-ops/hosts/lab-ops-01.json` に OS、取得時刻、パッケージとサービスの取得状態を保存。 | 「対象はこの VM `lab-ops-01` で合っていますか？ `web` の業務上の役割は何ですか？」 |
| 2. 調査・計画 | 現行版と候補のスナップショット、対象の成功条件・業務プローブ・復旧条件。`.server-ops/plans/web-check.json` と人向けの計画を作成。 | 「更新後に確認する業務 URL と期待応答は？ バックアップからの復旧試験は済んでいますか？」 |
| 3. 読み取り実行 | 利用者から今回の実行依頼と対象ホスト。最終計画 SHA はファイルから計算する。`.server-ops/runs/<plan-id>-<id>.json` に各コマンドと結果を保存。 | 対象や実行依頼が曖昧な場合だけ「`web-check-001` の読み取り計画を `lab-ops-01` で実行しますか？」 |

これらの回答が台帳・計画・会話に既にあれば、Agent は同じ内容を尋ね直さない。不足した場合は `askQuestion`（VS Code が提供する場合は `vscode/askQuestions`）を使い、回答まで該当段階を保留する。質問ツールがセッションで利用できないときは会話で尋ねる。認証情報や設定本文を質問しない。ツール名と利用可否は Copilot の Tools タブで確認する。[VS Code の Agent ツール参照](https://code.visualstudio.com/docs/agents/reference/tools-reference)。

例の計画本体は次の形式。`hostname` と `inventory_sha256` は実際の台帳に合わせて置き換える。`preconditions`・`verification`・`rollback` は説明欄であり、ランナーは自動判定しない。

```json
{
  "schema_version": 1,
  "plan_id": "web-check-001",
  "hostname": "lab-ops-01",
  "inventory_sha256": "<台帳ファイルの実際のSHA-256>",
  "purpose": "web の更新候補と稼働状況を確認する",
  "preconditions": ["Compose スナップショットの発行 unit と timer が稼働している"],
  "operations": [
    {"kind": "service-status", "target": "ssh.service"},
    {"kind": "compose-status"},
    {"kind": "compose-check", "target": "web"}
  ],
  "verification": ["候補版と取得時刻を読み、業務プローブは別途確認する"],
  "rollback": "読み取り操作のみ。追加変更は新しい計画と別の権限境界が必要"
}
```

root が発行するスナップショットの主要フィールドは次のとおり（時刻は説明用であり、この例を実行用に配置しない）。

```json
{
  "schema_version": 1,
  "publication_status": "complete",
  "hostname": "lab-ops-01",
  "status_observed_at": "2026-11-21T12:00:00+00:00",
  "status": {"web": {"enabled": true, "configured": "1.34.0", "auto_update": false, "interrupted": false}},
  "checks": {"web": {"status": "collected", "current": "1.34.0", "candidate": "1.34.1", "observed_at": "2026-11-21T12:00:01+00:00"}}
}
```

`1.34.0 → 1.34.1` は観測時点の候補であり、更新許可でも実行指示でもない。ランナーが終了 0 でも記録の `postconditions` は `not_assessed`。業務プローブや復旧確認を実施済みとは報告しない。

## 段階 1: 根拠付き構成図

`tools/build_topology.py` は、完全な単一ホスト台帳から Agent 向け `.json` と人向け `.md` を生成する。ネットワーク探索、リモート接続、設定本文の取り込みはしない。追加情報がなければ台帳取得時に稼働していたサービス名だけを一覧にし、**依存関係の図は未作成**とする。サービス名だけで接続先を推定しない。

```sh
python3 tools/build_topology.py \
  --inventory .server-ops/hosts/lab-ops-01.json \
  --output-prefix .server-ops/topology/lab-ops-01
```

利用者の確認や設定の限定的な観測によって関係が判明した場合は、`templates/topology-claims.example.json` を参考に `.server-ops/topology/lab-ops-01-claims.json` を作る。`hostname` と `inventory_sha256` を元の台帳に一致させ、node と edge に根拠の種類、参照名、時刻を記入する。例にある `web.service → db-01` は**仮の関係**で、台帳に `web.service` がなければ入力として拒否される。

```sh
python3 tools/build_topology.py \
  --inventory .server-ops/hosts/lab-ops-01.json \
  --claims .server-ops/topology/lab-ops-01-claims.json \
  --output-prefix .server-ops/topology/lab-ops-01-reviewed
```

JSON にはホスト、サービス、追加したノードと関係、各根拠、台帳 SHA、未評価範囲を含む。Markdown はサービス一覧、登録済み関係だけの Mermaid 図、根拠表、未評価範囲を示す。`hypothesis` の関係は破線で示す。`user-confirmed` と `config-observed` も根拠の種類として区別し、相互に読み替えない。出力は既存ファイルを上書きせず、専用の 0700 ディレクトリに 0600 で作る。実ホストの構成・内部名を含むため、公開リポジトリへコミットしない。サービス間の関係が未登録なら、必要な情報を Agent が利用者に質問する。

この情報は台帳を取得した時点の断面。登録した依存関係が現在も成立する保証や、ネットワーク全体を網羅した証明ではない。段階 2 の計画に使う前に対象・鮮度・根拠を再確認する。

## Codex / Claude Code の起動と段階別の確認

| 製品 | 主担当の入口 | 委任先 | Skill の配置 |
| --- | --- | --- | --- |
| Copilot | VS Code で `server-ops` Agent を選択 | `.github/agents/` の 3 役 | `.github/skills/` |
| Codex | ルートの `AGENTS.md` を読むセッションで「Server Ops Kit の段階 1」と依頼 | `.codex/agents/` の 3 役 | `.agents/skills/` |
| Claude Code | ルートで `claude --agent server-ops` | `.claude/agents/` の 3 役 | `.claude/skills/` |

Skill アダプターは製品に見つけてもらうための短い定義で、`.github/skills/<name>/SKILL.md` を参照する。スクリプト、テンプレート、ランナーは全製品で共有する。Codex のカスタム Agent はサブエージェントの定義であり、主担当は `AGENTS.md`。各製品で委任・Skill 選択・質問ツールの提供状況が違うため、起動時に読み込みを確認する。質問ツールがない場合は会話で必要な入力を尋ねる。

使い捨て VM の非特権アカウントで、最初は次の依頼から始める。対象のホスト名とローカル実行の可否が不明なら、Agent が確認する。コンテナ内で試した結果をホストの台帳として扱わない。

> この検証 VM を Server Ops Kit の段階 1 に登録したい。対象と目的を確認し、不足する入力は質問して。`linux-inventory` を使って読み取り台帳を作り、取得時刻・失敗した項目・未確認事項を報告して。計画作成と実行はまだ行わない。

台帳を確認した次のターンで、`lab-ops-01` の `ssh.service` 状態確認など、許可操作の読み取り計画を依頼する。さらに**別のターンで**対象ホストと計画 ID を指定して読み取り実行を依頼する。計画や SHA の生成を同一指示で実行許可とみなさない。実行結果の `postconditions: not_assessed`、追加作業の計画への差戻し、Docker・sudo の権限拒否を確認する。Compose の確認は root 発行スナップショットの配置と timer の健全性を管理者が確認した VM だけで試す。実ホストや既存サービスを使わない。

Codex のサブエージェント定義は独立した読み取り専用サンドボックスを作らず、親セッションの権限を継承する。Claude Code も親の許可モードによって Agent 側の制限が上書きされ得る。両者の動作確認と OS 権限の分離が済むまで本番運用に進めない。

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

計画の SHA は**最終編集後**に計算する。`run_plan.py` はホスト名、台帳のハッシュ、取得範囲・完全性・鮮度を照合し、未知の操作・任意のシェル文字列を拒否する。SHA 照合は変更検出であり、人の承認を証明しない。Agent の端末権限自体は OS の権限境界ではない。管理権限や Docker ソケットへアクセスできない検証用アカウントでのみ使用する。読み取りの外部コマンドにも副作用や通信の可能性があり、各ツールの設定を事前に確認する。

実行記録は開始前から 0600 で保存し、各操作を `not_executed` / `running` / `completed` / `failed` / `result_unknown` に分ける。SIGINT/SIGTERM 中断後の対象状態は `result_unknown` として現物を照合する。SIGKILL・電源断では、終了時刻なしの `running` が残るか、記録自体が失われる可能性がある。これも結果不明とし、元の操作、対象の現物、updater の journal を管理者が照合するまで再実行しない。終了コード 0 でも外形・業務条件は `postconditions: not_assessed` のまま。更新の成功や本番運用可否を意味しない。

## 配置

キット全体（`.github/`、`.codex/`、`.agents/`、`.claude/`、`tools/`、`templates/`、`tests/`、`README.md`、`AGENTS.md`、`CLAUDE.md`、`.gitignore`）を Copilot の対象リポジトリへ配置する。管理ホストに Python 3.11 以降が必要。実行時データは `.server-ops/` に保存し Git へコミットしない。収集 CLI が終了 2 のときは部分取得なので、記録を確認し再収集する。コンテナ内の収集結果をホストの台帳として使わない。

### Compose の非特権読み取り

`svc-update status/check` は root 専用の設定とバックアップ先ロックを開くため、Copilot の非特権アカウントから直接起動しない。管理者が `tools/compose_snapshot.py publish` を定期実行し、登録済みサービスの現在版、候補版、更新設定、中断有無だけを `/var/lib/server-ops-kit/compose-state/snapshot.json` に発行する。ランナーの `compose-status/check` はこのファイルだけを読み、30分を超えた結果、他ホストの結果、未知のサービス、失敗したチェック、updater の版形式と一致しない候補を拒否する。発行開始時に旧結果を無効化し、ロック競合などで発行に失敗した後も旧候補を返さない。読取時は systemd の timer が active で発行 unit の Result が success であることも確認する。停電などで発行プロセスが途中停止した場合は無効化済みの状態が残る。上位ディレクトリも root 所有で group/others が書込不可であることを確認する。候補は定期観測値であり、現在も更新可能という保証ではない。

管理者が検証 VM で以下を実施する。`kit-reader` は例として Copilot 専用ユーザーの名前。`/opt/server-ops-kit` 以下のコード、unit、updater の設定は root 所有で書き込み不可にする。updater の root 専用設定・Docker ソケット・バックアップ先への権限を `kit-reader` に与えない。

```sh
install -d -o root -g root -m 0755 /opt/server-ops-kit/tools
install -o root -g root -m 0644 tools/compose_snapshot.py tools/periodic_report.py /opt/server-ops-kit/tools/
groupadd --system server-ops-reader
usermod -aG server-ops-reader kit-reader
install -d -o root -g server-ops-reader -m 0750 /var/lib/server-ops-kit/compose-state
install -o root -g root -m 0644 templates/systemd/server-ops-compose-snapshot.service /etc/systemd/system/
install -o root -g root -m 0644 templates/systemd/server-ops-compose-snapshot.timer /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/server-ops-compose-snapshot.{service,timer}
systemctl daemon-reload
systemctl start server-ops-compose-snapshot.service
systemctl enable --now server-ops-compose-snapshot.timer
```

上記は root で実行し、`kit-reader` はグループ変更後に再ログインする。実環境の updater パス（標準 `/usr/local/bin/svc-update`）、設定 `/etc/svc-update/config.toml`、バックアップルートと unit の `ReadWritePaths` を一致させる。root タイマーは固定の登録名だけを照会し、ユーザー入力を特権コマンドへ渡さない。配置後、`kit-reader` で `python3 tools/compose_snapshot.py read status` と `read check <登録名>` が動き、`svc-update status`、Docker ソケット、`sudo -n -l` には変更権限がないことを確認する。root タイマーが失敗・遅延すると読み取りも停止する。

定期レポートは `python3 tools/periodic_report.py --kind performance --output-dir .server-ops/reports` で送信せずに試す。セキュリティ報告も `--kind security` で試す。必要な配布元ツールとメタデータが使えなければ「未評価」で終了する。警告が出た場合も未評価とし、鮮度は別途確認する。送信する場合は `templates/report.example.json` を対象ホストの `/etc/server-ops-kit/report.json` に置いて宛先を設定し、専用アカウントにのみ読ませる。レポート用の `templates/systemd/` は `/opt/server-ops-kit`、レポート格納先 `/var/lib/server-ops-kit/reports`、ローカル時刻の 09:00/09:15 を前提にした**編集用ひな形**。対象ホストのパス・権限・タイムゾーン・SMTP リレーを確認してから導入する。事前に専用ユーザー `server-ops-reporter`、設定ディレクトリを用意し、`install -d -o server-ops-reporter -g server-ops-reporter -m 0700 /var/lib/server-ops-kit/reports` として記録ディレクトリを作る。コードは root 所有でレポーターが変更できないようにする。設定は root:server-ops-reporter の 0640 などで必要最小限に読ませ、`systemd-analyze verify` と送信先が無効なテスト環境で試す。メール送信は `--send` を指定したときだけ行い、部分受理時は記録の受理・拒否件数を見て再送範囲を判断する。

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

## 対象別の読み取り調査

段階 2 で対象に応じて `systemd-inspect`、`docker-inspect`、`vm-inspect`（VirtualBox）、`db-inspect` を使う。Docker は既存の Compose スナップショットと、管理者が機密情報を除いて提供した抜粋を参照する。Kubernetes は検証環境が揃うまで保留。

複数人での調査は [調査記録と引き継ぎ](docs/investigation-record.md) に従う。記入例は [investigation.example.json](templates/investigation.example.json)。

## cron でディスク・CPU・メモリを監視する

`templates/disk-monitor.example.json` を専用の非特権ユーザー向けにコピーし、対象のホスト ID・マウント・担当者・宛先を設定する。5 分間隔で 90% 以上を 3 回連続観測すると、ホストとマウントの組ごとに案件を起票する。設定より長い採取間隔や採取失敗で連続回数をリセットする。採取失敗は別案件として通知し、既存の容量案件は正常値を観測した時点で閉じる。

`resource_targets` は任意。監視するホストごとに 1 件登録し、`thresholds` に `cpu`、`memory` または両方の閾値（整数の 1〜100%）を設定する。例では両方とも 90% で、連続回数と最大採取間隔はディスクと共通。CPU・メモリの案件はホストと指標の組ごとに独立して起票・復旧を判定する。

- CPU は /proc/stat の全 CPU の累積時間から直前の採取との差分を取り、idle と iowait を除いた時間の割合を使用する。初回や再起動、採取間隔の上限超過後は基準値を採取する。5 分間隔で 3 回の連続超過を判定するには、初回から最低 4 回の採取が必要。
- メモリは /proc/meminfo の `MemTotal` と `MemAvailable` から `(MemTotal - MemAvailable) / MemTotal × 100` を求める。必要な項目が欠けている場合は採取失敗として記録する。

- リモートは監視専用 SSH 設定の登録済み alias を使い、既知のホスト鍵を事前登録する。ディスク用の専用鍵は従来通り `LC_ALL=C df -Pk`、CPU・メモリ用の別の専用鍵は `LC_ALL=C cat /proc/stat /proc/meminfo` をサーバ側の強制コマンドに指定する。設定例ではそれぞれに別の alias を割り当てている。両方の鍵で TTY と転送を禁止する。設定と鍵は保守担当が管理する。
- Agent を有効化するには、設定の `codex_binary` に Codex CLI の絶対パスを指定する。認証と通信先を確認した専用実行環境を用意する。監視用鍵を見せないため、実運用では分離した環境へ渡す管理者所有のラッパーを指定できる。Agent には対象マウントまたは指標名と数値の推移を渡す。出力は原因候補と追加確認案として扱う。
- `--send` を付けると、対象の担当者宛に設定済みの SMTP リレーで通知する。Agent の失敗・タイムアウト時も観測結果を通知する。リレー受付と最終配送は別。二重通知を避けるため、同一案件での送信失敗・送信結果不明は記録し、再送は運用担当が判断する。

試行と本運用では別の状態ディレクトリを使う。通知なしで始める場合は `--send` を外す。Agent への送信も無効にする場合は `codex_binary` を設定しない。

```cron
*/5 * * * * /usr/bin/python3 /opt/server-ops-kit/tools/monitor_disk.py --config /etc/server-ops-kit/disk-monitor.json --state-dir /var/lib/server-ops-monitor --send
```

開始前に、同じユーザーで単発実行して設定、SSH、出力権限、テスト用メールを確認する。案件 JSON と状態は専用の 0700 ディレクトリに保存する。重複実行はスキップする。
