# Utilization review v1

This directory contains an advisory post-draft utilization review. The source
roster is the completed October 10 practice observation. It is a real captured
roster from a practice room, but it contains no dated daily schedule or health
matrix, and every player record has at least one missing scoring field.

`analysis.json` therefore reports structural legality and two explicit,
synthetic one-day stress cases. The stress cases are mechanics tests: one turns
on only the four C-only players and the other turns on only the three
non-center players. They do not estimate the team's real weekly starts.

The review implementation is `draft_assistant/utilization_review.py`. It uses
exact matching for C/G/G/F/F/UTIL, gives active starts priority, then scores,
and exposes a separate advisory candidate ranker. It is intentionally not
imported by the live scorer or coordinator.

The frozen foundation/lookahead validation artifacts were read for context and
left unchanged. The recommended next input is a dated schedule and health
matrix; only then should the marginal usable value be evaluated on real
weekly scenarios.
