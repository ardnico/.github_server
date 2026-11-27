# Server Ops Kit: Claude Code の入口

`AGENTS.md` の共通運用契約を先に読み、同じ三段階で進める。主担当として使う場合は `claude --agent server-ops` をリポジトリのルートで起動する。`.claude/agents/server-ops.md` はこの主担当の定義。

必要な入力は利用可能な `AskUserQuestion` でまとめて確認し、利用できなければ通常の会話で尋ねる。委任先は質問ツールを使えないため、不足情報を主担当に返す。実行依頼を推測しない。

Claude Code のツール許可や Agent の指示は OS の権限境界ではない。sudo、Docker ソケット、updater の設定やバックアップを触れない検証アカウントを使う。権限を広げたり、自動更新・メール送信を勝手に有効化したりしない。
