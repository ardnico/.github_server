---
name: compose-update-manager
description: ardnico/compose-update-manager を使い Docker Compose の公開 Hub イメージまたはローカルビルドの更新を調査・計画するときに使う。
---

# Compose 更新連携

本 Kit では更新の自動適用を無効化している。`svc-update upgrade --apply` や `restore --apply` を Agent の端末権限から呼び出さない。手動の変更計画には、対象ストレージ構成での手動・異常時復旧の実証と、承認・実行アカウントの分離を前提条件として明記する。

正本の `https://github.com/ardnico/compose-update-manager/blob/main/README.md` と `doc/README.md` を作業時に再確認する。v0.3 preview には `local_build`、`keep_previous_images`、`check`、対象サービスの `upgrade --apply`、format 3 の対象別バックアップがある。自身で更新処理を再実装しない。

1. 非特権 Agent は root が定期発行する Compose スナップショットだけを `tools/run_plan.py` の `compose-status/check` 経由で参照する。`svc-update status/check` を直接起動しない。30分以内の観測か確認する。登録名と Compose の service key を混同しない。
2. `local_build` なら base_argument、Compose build.args、Dockerfile の ARG/FROM、出力 image を照合する。固定タグと版付き出力の復旧経路を確認する。
3. `keep_previous_images`、バックアップ先、データマウントの共有、ヘルスチェック、過去の対象別復旧試験を計画へ記載する。v0.2 と v0.3 のバックアップ互換を仮定しない。
4. 手動更新を検証してからタイマーを検討。定期実行の登録は別の作業計画とする。Docker 操作権限は root 相当として扱う。
