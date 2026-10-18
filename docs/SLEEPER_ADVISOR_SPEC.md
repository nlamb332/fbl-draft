# Sleeper advisor — implementation spec

Branch `sleeper-advisor`. A second, independent advisor for Sleeper NBA drafts
(e.g. `https://sleeper.com/draft/nba/<draft_id>`). It lives in a new top-level
package `sleeper_advisor/` and does **not** modify `draft_assistant/` behavior.

## Observed differences from the ESPN advisor

| Concern | ESPN advisor (`draft_assistant`) | Sleeper advisor |
| --- | --- | --- |
| Room data | Scraped from the ESPN DOM by a bookmarklet / page runner; virtualized grid scans, practice pause | Public read-only JSON API `https://api.sleeper.app/v1/...`; no login, no DOM, no bookmarklet, no browser control |
| Draft settings | Read from ESPN Rules view | `GET /v1/draft/{id}` → `settings` (`teams`, `rounds`, `slots_pg/sg/g/sf/pf/f/c/util/bn`, `pick_timer`, optional `reversal_round`), `type` (`snake`/`linear`), `status` (`pre_draft`/`drafting`/`paused`/`complete`), `draft_order` (user_id → slot, may be null pre-draft), `slot_to_roster_id`, `metadata.type` (`league_mock`, etc.), `metadata.league_id` |
| Scoring | Hard-coded FBL formula (FGM/FGA, PF, etc.) | `GET /v1/league/{league_id}` → `scoring_settings` (e.g. `pts 0.5, reb 1, ast 1, stl 2, blk 2, to -1, tpm 0.5, dd 1, td 2, bonus_pt_40p 2, bonus_pt_50p 2, tf -2, ff -2`). If the draft has no league, fall back to Sleeper default points (same as above). |
| Roster | C, G, G, F, F, UTIL + 4 BN, 14 teams | Derived from draft `settings.slots_*`; the observed league is PG, SG, G, SF, PF, F, C, UTIL, UTIL + 5 BN, 6 teams, 14 rounds |
| Eligibility | ESPN C/G/F classes | Native positions from player `fantasy_positions` (PG/SG/SF/PF/C). Slot rules: PG/SG/SF/PF/C exact; G accepts PG or SG; F accepts SF or PF; UTIL accepts any; BN not a starting slot |
| Picks | Scraped pick history | `GET /v1/draft/{id}/picks` → list of `{pick_no, round, draft_slot, roster_id, picked_by (user_id, "" for CPU), player_id, metadata{first_name,last_name,position,team}}`; `GET /v1/draft/{id}/traded_picks` |
| Our team | ESPN team id/name in config | Sleeper username → `GET /v1/user/{username}` → `user_id`; our slot = `draft_order[user_id]`. If `draft_order` is null, fall back to `--slot N` or the slot of picks already made by `picked_by == user_id`; otherwise "slot unknown" (still recommend best available). |
| Players | ESPN scan cache in browser localStorage | `GET /v1/players/nba` (~2.5 MB dict keyed by player_id; cache to `runtime/sleeper/players_nba.json`, refresh if older than 24 h) |
| Projections | ESPN projections scraped from the grid | `GET https://api.sleeper.app/projections/nba/{season}?season_type=regular&position[]=PG&position[]=SG&position[]=SF&position[]=PF&position[]=C` (list; each row has `player_id`, `player{fantasy_positions,injury_status,team,first_name,last_name}`, `stats` per-game: `pts reb ast stl blk to tpm dd td fgm fga ftm fta sp gp adp_std adp_dynasty`). `gp` is always 1 (per-game basis). `sp` ≈ projected season minutes. ~530 rows have stat projections; ~1860 have only ADP. Missing `dd`/`td` means 0. Cache to `runtime/sleeper/projections_{season}.json` (refresh if older than 6 h). Responses are UTF-8 (names contain `ć` etc.) — always decode as UTF-8 and never print with the Windows console default codec. |
| Draft type | Real vs practice room attestation | `metadata.type == "league_mock"` / `"mock"` vs a league draft; display it, no special control flow |

## Package layout and interfaces

All code is Python 3.10+, standard library only (`urllib.request`, `json`,
`http.server`, `dataclasses`). Network access is always through an injectable
`fetch_json(url) -> Any` callable so tests never hit the network.

### `sleeper_advisor/api.py` (agent A)
- `class SleeperClient(fetch_json=None, cache_dir=Path("runtime/sleeper"), now=time.time)`
  - `draft(draft_id) -> dict`, `picks(draft_id) -> list`, `traded_picks(draft_id) -> list`
  - `league(league_id) -> dict`, `user(username) -> dict`
  - `players() -> dict` (24 h file cache), `projections(season) -> list` (6 h file cache)
- `parse_draft_id(url_or_id: str) -> str` — accepts `https://sleeper.com/draft/nba/<id>?...`, `sleeper.app/draft/nba/<id>`, or a bare numeric id; raises `ValueError` otherwise.
- Default fetcher: `urllib.request` with a `User-Agent`, 15 s timeout, UTF-8 decode.

### `sleeper_advisor/model.py` (agent A)
```python
@dataclass(frozen=True)
class SleeperLeague:
    draft_id: str; league_id: str | None; season: str; draft_type: str  # snake|linear
    mock_type: str | None; status: str; teams: int; rounds: int
    starting_slots: tuple[str, ...]   # e.g. ("PG","SG","G","SF","PF","F","C","UTIL","UTIL")
    bench_slots: int; reversal_round: int | None
    scoring: dict[str, float]          # Sleeper scoring keys, lowercase

@dataclass(frozen=True)
class SleeperPlayer:
    player_id: str; name: str; positions: tuple[str, ...]  # native, ordered PG,SG,SF,PF,C
    nba_team: str | None; injury_status: str | None
    stats: dict[str, float]            # per-game projection stats (lowercase keys)
    adp: float | None                  # adp_std
    has_projection: bool

@dataclass(frozen=True)
class DraftState:
    league: SleeperLeague
    our_slot: int | None
    current_pick: int | None           # next pick number (1-based) or None if complete
    on_clock_slot: int | None
    picks: tuple[dict, ...]            # raw picks sorted by pick_no
    drafted_ids: frozenset[str]
    our_roster: tuple[SleeperPlayer, ...]
    available: tuple[SleeperPlayer, ...]  # undrafted players with a projection or ADP
    our_next_picks: tuple[int, ...]    # our remaining pick numbers, ascending
    warnings: tuple[str, ...]
```
- `parse_league(draft: dict, league: dict | None) -> SleeperLeague` — slot order PG, SG, G, SF, PF, F, C, UTIL from `slots_*` counts; `slots_bn` → bench. Default scoring when league missing.
- `build_players(players: dict, projections: list) -> dict[str, SleeperPlayer]` — positions from projection `player.fantasy_positions`, else players `fantasy_positions`; drop players with no PG/SG/SF/PF/C eligibility.
- `pick_slot(pick_no, teams, draft_type, reversal_round=None) -> int` and `slot_pick_numbers(slot, teams, rounds, draft_type, reversal_round=None) -> tuple[int, ...]`. Snake: odd rounds 1..N, even rounds N..1. With `reversal_round = r` (3RR), rounds ≥ r flip the snake parity once (round r goes same direction as round r-1). Linear: every round 1..N. Traded picks: `traded_picks` entries `{round, roster_id (original), owner_id (new)}` reassign ownership; account for them in `our_next_picks` using `slot_to_roster_id`.
- `build_state(client, draft_id, username=None, slot=None) -> DraftState`.

### `sleeper_advisor/scoring.py` (agent B)
- `fantasy_points(stats: dict, scoring: dict) -> tuple[float, dict[str, float], tuple[str, ...]]` — per-game FP, per-key contributions, warnings. Direct keys: `pts reb ast stl blk to tpm dd td` plus any other scoring key present in stats (e.g. `fgm`, `fga`, `ftm`, `fta`, `oreb`, `dreb`, `pf`). Bonus/penalty keys not projected (`bonus_pt_40p`, `bonus_pt_50p`, `tf`, `ff`, `qd`, ...) contribute 0 and add one warning each. `dd`/`td` missing in stats means 0 (projections omit zeros), no warning. DD and TD stack (a TD also earns DD) — Sleeper's projected `dd` rate already includes TD games; do not add TD into DD.

### `sleeper_advisor/lineup.py` (agent B)
- `SLOT_ACCEPTS = {"PG":{"PG"},"SG":{"SG"},"SF":{"SF"},"PF":{"PF"},"C":{"C"},"G":{"PG","SG"},"F":{"SF","PF"},"UTIL":{"PG","SG","SF","PF","C"}}`
- `best_lineup(players: Sequence[tuple[str, tuple[str,...], float]], slots: Sequence[str]) -> tuple[float, dict[str, str]]` — exact max-weight assignment (greedy descending value with augmenting-path matchability check is exact: transversal matroid). Returns total and player_id → slot.
- Positions are never capped or quota'd; value comes only from lineup scoring (owner rule: no position caps).

### `sleeper_advisor/advisor.py` (agent B)
- `recommend(state: DraftState, top_n=10) -> list[Recommendation]` where
  `@dataclass(frozen=True) class Recommendation: player_id, name, positions, score, fp_per_game, adp, components: dict[str,float], explanation: str, warnings: tuple[str,...]`.
- Method (deterministic):
  1. Each available player's value `v = fantasy_points(...)` per game (players with ADP but no projection get `v = 0` and a warning; they sort last).
  2. **Expected roster completion**: for our remaining picks after this one, assume players taken in ADP order (ties/unknown ADP by FP) by other teams between our picks; at each of our future picks we take the best remaining player by marginal lineup gain. Limit to the roster size (starting + bench).
  3. **Score** of candidate X = (starting-lineup total of our roster + X + expected completion) − (same with a replacement pick instead of X, where the replacement is the best player expected to survive to our next pick), plus a small bench term: `0.15 ×` the FP of the best non-starter (depth for injuries/rest). Evaluate the top ~25 candidates by FP and ADP union, within a 2 s budget.
  4. `components` includes `fp_per_game`, `lineup_gain`, `survival` (estimated probability X is still available at our next pick from ADP vs picks in between: logistic on `(adp − next_pick)/max(3, 0.15·next_pick)`), `bench_term`. `explanation` is one short sentence (e.g. "Fills SG; 41.2 FP/g; likely gone before pick 18 (ADP 9.4)").
  5. When `our_slot` is unknown, assume the next pick is ours and that our next pick is `teams` picks later.

### `sleeper_advisor/__main__.py`, `cli.py`, `panel.py` (agent C, after A and B)
- `python -m sleeper_advisor watch <draft_url_or_id> [--user USERNAME] [--slot N] [--interval 3] [--top 10]` — polls draft + picks every interval; recomputes only when `pick_no` count or status changes; prints a compact table (UTF-8 safe: reconfigure stdout to UTF-8 with `errors="replace"`) and a banner when we are on the clock or our pick is within 2.
- `python -m sleeper_advisor serve <draft_url_or_id> [--user ...] [--port 8766]` — 127.0.0.1-only stdlib HTTP server: `GET /` small self-refreshing HTML page (league summary, our roster by slot, on-clock indicator, top recommendations table), `GET /api/state` JSON. No POST routes, no CORS, binds loopback only.
- `python -m sleeper_advisor once <draft> [--json]` — single snapshot.
- `Launch Sleeper Advisor.cmd` at repo root: prompts for the draft URL and Sleeper username (remembered in ignored `runtime/sleeper/config.json`), runs `serve`, opens the browser. Mirror the friendly Python checks in `Launch Advisor.cmd`.
- `docs/SLEEPER_ADVISOR.md` user guide; README gets a short "Sleeper advisor" section.

## Tests
- Live under `sleeper_advisor/tests/` (pytest-style unittest), fixtures under `sleeper_advisor/tests/fixtures/` — **synthetic and sanitized**: fake IDs, generic names ("Example League", "user_a"), a small players/projections set (~60 players covering all positions). Do not copy real league names, user IDs, or the downloaded full players file.
- `.gitattributes`: add `/sleeper_advisor/tests export-ignore` and keep `docs/SLEEPER_ADVISOR.md` in the archive (`-export-ignore`); leave this spec export-ignored.
- Must pass: `python -m pytest sleeper_advisor -q` and the existing `python -m pytest draft_assistant mock_draft -q` (unchanged).
