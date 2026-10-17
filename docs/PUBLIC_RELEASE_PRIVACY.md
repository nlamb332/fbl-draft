# Publication privacy

Application defaults and checked-in configuration examples use generic league
and team identities. On first launch, the Windows launcher asks for your ESPN
league ID, season ID, team ID, and exact team name. It stores them only in the
ignored `runtime/league.json` file. Do not add runtime files or browser state
to Git. CLI users can pass `--league-config runtime/league.json` before the
subcommand.

Captured evidence and compressed study artifacts have been anonymized. The
two private browser screenshots are omitted. Public player names, forecasts,
scoring rules, and numerical study results remain available.

The publication review includes a Gitleaks credential scan of Git history and
nested archives, an explicit personal-information review, and runtime/archive
checks. A scan reduces risk; it cannot prove that no sensitive information
exists. New contributions should receive the same review before publication.

Keep earlier private clones and recovery backups private. They may contain
unsanitized files or commit metadata even when the publication snapshot is clean.
