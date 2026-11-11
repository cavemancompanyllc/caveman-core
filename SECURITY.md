# Security

Please report vulnerabilities privately to admin@cavemancompany.co rather than
opening a public issue. Include the version (`cavecore --version`), what you
found, and how to reproduce it.

Design commitments this project holds itself to:

- Plugins and the `cavecore` CLI never store secrets in plugin folders, the
  ledger, or backups. Secrets live in a workspace's gitignored `.env` or in
  Claude Code's credential store via `userConfig` with `sensitive: true`.
- `cavecore backup` skips browser profiles and files whose names suggest tokens,
  credentials or cookies.
- `cavecore ledger sql` opens the database read-only.
- Content read from outside the workspace (web pages, email, files) is treated
  as data, never instructions; see the `untrusted-content` skill.
