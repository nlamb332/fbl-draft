"""Draft/league/player model and draft-order math for the Sleeper advisor."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .api import POSITIONS, SleeperClient

SLOT_ORDER = ("PG", "SG", "G", "SF", "PF", "F", "C", "UTIL")

DEFAULT_SCORING: dict[str, float] = {
    "ast": 1.0,
    "blk": 2.0,
    "bonus_pt_40p": 2.0,
    "bonus_pt_50p": 2.0,
    "dd": 1.0,
    "ff": -2.0,
    "pts": 0.5,
    "reb": 1.0,
    "stl": 2.0,
    "td": 2.0,
    "tf": -2.0,
    "to": -1.0,
    "tpm": 0.5,
}

_ADP_KEYS = ("adp_std", "adp_dynasty")


@dataclass(frozen=True)
class SleeperLeague:
    draft_id: str
    league_id: str | None
    season: str
    draft_type: str  # snake|linear
    mock_type: str | None
    status: str
    teams: int
    rounds: int
    starting_slots: tuple[str, ...]
    bench_slots: int
    reversal_round: int | None
    scoring: dict[str, float]


@dataclass(frozen=True)
class SleeperPlayer:
    player_id: str
    name: str
    positions: tuple[str, ...]
    nba_team: str | None
    injury_status: str | None
    stats: dict[str, float]
    adp: float | None
    has_projection: bool


@dataclass(frozen=True)
class DraftState:
    league: SleeperLeague
    our_slot: int | None
    current_pick: int | None
    on_clock_slot: int | None  # slot of the roster that owns the current pick
    picks: tuple[dict, ...]
    drafted_ids: frozenset[str]
    our_roster: tuple[SleeperPlayer, ...]
    available: tuple[SleeperPlayer, ...]
    our_next_picks: tuple[int, ...]
    warnings: tuple[str, ...]


# -- small coercion helpers ---------------------------------------------
def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and out not in (float("inf"), float("-inf")) else None


def _int(value: Any) -> int | None:
    number = _num(value)
    return None if number is None else int(number)


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# -- league ----------------------------------------------------------------
def parse_league(draft: Mapping[str, Any], league: Mapping[str, Any] | None) -> SleeperLeague:
    draft = draft or {}
    settings = draft.get("settings") or {}
    metadata = draft.get("metadata") or {}
    league = league or None

    slots: list[str] = []
    for slot in SLOT_ORDER:
        slots.extend([slot] * max(0, _int(settings.get(f"slots_{slot.lower()}")) or 0))
    bench = _int(settings.get("slots_bn"))
    if not slots and league and league.get("roster_positions"):
        roster_positions = [str(p).upper() for p in league["roster_positions"]]
        slots = [p for p in roster_positions if p in SLOT_ORDER]
        if bench is None:
            bench = sum(1 for p in roster_positions if p == "BN")
    bench = bench or 0

    teams = _int(settings.get("teams")) or _int((league or {}).get("total_rosters")) or 0
    reversal = _int(settings.get("reversal_round"))
    scoring_raw = (league or {}).get("scoring_settings")
    scoring = (
        {str(k).lower(): float(v) for k, v in scoring_raw.items() if _num(v) is not None}
        if isinstance(scoring_raw, Mapping) and scoring_raw
        else dict(DEFAULT_SCORING)
    )
    season = draft.get("season") or (league or {}).get("season") or ""
    return SleeperLeague(
        draft_id=str(draft.get("draft_id") or ""),
        league_id=_str_or_none(draft.get("league_id")) or _str_or_none(metadata.get("league_id")),
        season=str(season),
        draft_type=str(draft.get("type") or "snake").lower(),
        mock_type=_str_or_none(metadata.get("type")),
        status=str(draft.get("status") or "unknown"),
        teams=teams,
        rounds=_int(settings.get("rounds")) or 0,
        starting_slots=tuple(slots),
        bench_slots=bench,
        reversal_round=reversal if reversal and reversal > 0 else None,
        scoring=scoring,
    )


# -- players -----------------------------------------------------------------
def _ordered_positions(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)):
        return ()
    have = {str(p).upper() for p in raw}
    return tuple(p for p in POSITIONS if p in have)


def _name(first: Any, last: Any, full: Any = None) -> str:
    full = _str_or_none(full)
    if full:
        return full
    return " ".join(part for part in (_str_or_none(first), _str_or_none(last)) if part)


def build_players(
    players: Mapping[str, Any] | None, projections: Sequence[Mapping[str, Any]] | None
) -> dict[str, SleeperPlayer]:
    players = players or {}
    proj_by_id: dict[str, Mapping[str, Any]] = {}
    for row in projections or []:
        pid = _str_or_none((row or {}).get("player_id"))
        if pid:
            proj_by_id[pid] = row

    result: dict[str, SleeperPlayer] = {}
    for pid in list(players) + [p for p in proj_by_id if p not in players]:
        base = players.get(pid) or {}
        row = proj_by_id.get(pid) or {}
        proj_player = row.get("player") or {}
        positions = _ordered_positions(proj_player.get("fantasy_positions")) or _ordered_positions(
            base.get("fantasy_positions")
        )
        if not positions:
            continue
        raw_stats = row.get("stats") or {}
        stats = {
            str(k).lower(): v
            for k, v in ((k, _num(v)) for k, v in raw_stats.items())
            if v is not None and k not in _ADP_KEYS
        }
        name = _name(
            proj_player.get("first_name") or base.get("first_name"),
            proj_player.get("last_name") or base.get("last_name"),
            base.get("full_name"),
        )
        result[str(pid)] = SleeperPlayer(
            player_id=str(pid),
            name=name or f"Player {pid}",
            positions=positions,
            nba_team=_str_or_none(proj_player.get("team")) or _str_or_none(base.get("team")),
            injury_status=_str_or_none(proj_player.get("injury_status"))
            or _str_or_none(base.get("injury_status")),
            stats=stats,
            adp=_num(raw_stats.get("adp_std")),
            has_projection=bool(stats),
        )
    return result


# -- draft order ---------------------------------------------------------------
def _round_and_index(pick_no: int, teams: int) -> tuple[int, int]:
    if teams <= 0 or pick_no < 1:
        raise ValueError("pick_no must be >= 1 and teams > 0")
    return (pick_no - 1) // teams + 1, (pick_no - 1) % teams


def pick_slot(pick_no: int, teams: int, draft_type: str, reversal_round: int | None = None) -> int:
    rnd, idx = _round_and_index(pick_no, teams)
    if str(draft_type).lower() == "linear":
        return idx + 1
    forward = rnd % 2 == 1
    if reversal_round and rnd >= reversal_round:
        forward = not forward
    return idx + 1 if forward else teams - idx


def slot_pick_numbers(
    slot: int, teams: int, rounds: int, draft_type: str, reversal_round: int | None = None
) -> tuple[int, ...]:
    return tuple(
        n
        for n in range(1, teams * rounds + 1)
        if pick_slot(n, teams, draft_type, reversal_round) == slot
    )


def _slot_to_roster(slot_to_roster_id: Mapping[Any, Any] | None) -> dict[int, int]:
    out: dict[int, int] = {}
    for k, v in (slot_to_roster_id or {}).items():
        slot, roster = _int(k), _int(v)
        if slot is not None and roster is not None:
            out[slot] = roster
    return out


def roster_pick_numbers(
    roster_id: int,
    teams: int,
    rounds: int,
    draft_type: str,
    reversal_round: int | None = None,
    slot_to_roster_id: Mapping[Any, Any] | None = None,
    traded_picks: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[int, ...]:
    """Pick numbers owned by ``roster_id`` after applying traded picks."""
    s2r = _slot_to_roster(slot_to_roster_id)
    owners: dict[tuple[int, int], int] = {}
    for trade in traded_picks or []:
        rnd, original, owner = _int(trade.get("round")), _int(trade.get("roster_id")), _int(trade.get("owner_id"))
        if None not in (rnd, original, owner):
            owners[(rnd, original)] = owner
    mine = []
    for n in range(1, teams * rounds + 1):
        rnd, _ = _round_and_index(n, teams)
        slot = pick_slot(n, teams, draft_type, reversal_round)
        original = s2r.get(slot, slot)
        if owners.get((rnd, original), original) == roster_id:
            mine.append(n)
    return tuple(mine)


def _pick_owner_roster(
    pick_no: int, league: SleeperLeague, s2r: Mapping[int, int], traded_picks: Sequence[Mapping[str, Any]]
) -> int:
    rnd, _ = _round_and_index(pick_no, league.teams)
    slot = pick_slot(pick_no, league.teams, league.draft_type, league.reversal_round)
    original = s2r.get(slot, slot)
    owner = original
    for trade in traded_picks:
        if _int(trade.get("round")) == rnd and _int(trade.get("roster_id")) == original:
            new_owner = _int(trade.get("owner_id"))
            if new_owner is not None:
                owner = new_owner
    return owner


# -- state ---------------------------------------------------------------------
def build_state(
    client: SleeperClient, draft_id: str, username: str | None = None, slot: int | None = None
) -> DraftState:
    warnings: list[str] = []
    draft = client.draft(draft_id) or {}
    picks_raw = client.picks(draft_id) or []
    try:
        traded = [t for t in (client.traded_picks(draft_id) or []) if isinstance(t, Mapping)]
    except Exception as exc:  # traded picks are optional
        traded = []
        warnings.append(f"Could not load traded picks ({exc}); assuming none.")

    metadata = draft.get("metadata") or {}
    league_id = _str_or_none(draft.get("league_id")) or _str_or_none(metadata.get("league_id"))
    league_raw = None
    if league_id:
        try:
            league_raw = client.league(league_id)
        except Exception as exc:
            warnings.append(f"Could not load league {league_id} ({exc}); using default Sleeper scoring.")
    else:
        warnings.append("Draft has no league; using default Sleeper scoring.")
    league = parse_league(draft, league_raw)
    if league.teams <= 0 or league.rounds <= 0:
        warnings.append("Draft settings missing teams/rounds; pick math unavailable.")

    picks = tuple(
        dict(p)
        for p in sorted(
            (p for p in picks_raw if isinstance(p, Mapping) and _int(p.get("pick_no")) is not None),
            key=lambda p: _int(p["pick_no"]),
        )
    )
    s2r = _slot_to_roster(draft.get("slot_to_roster_id"))
    r2s = {r: s for s, r in s2r.items()}

    # our identity
    user_id: str | None = None
    if username:
        try:
            user_id = _str_or_none((client.user(username) or {}).get("user_id"))
            if not user_id:
                warnings.append(f"Sleeper user {username!r} not found.")
        except Exception as exc:
            warnings.append(f"Could not look up Sleeper user {username!r} ({exc}).")

    our_slot: int | None = None
    draft_order = draft.get("draft_order") or {}
    if user_id and user_id in draft_order:
        our_slot = _int(draft_order[user_id])
        if slot is not None and slot != our_slot:
            warnings.append(f"--slot {slot} ignored; draft order puts {username} in slot {our_slot}.")
    if our_slot is None and slot is not None:
        our_slot = slot
    if our_slot is None and user_id:
        mine = [p for p in picks if _str_or_none(p.get("picked_by")) == user_id]
        rosters = Counter(r for r in (_int(p.get("roster_id")) for p in mine) if r is not None)
        if rosters:
            roster_id = rosters.most_common(1)[0][0]
            our_slot = r2s.get(roster_id, roster_id if not s2r else None)
        if our_slot is None and mine:
            slots = Counter(s for s in (_int(p.get("draft_slot")) for p in mine) if s is not None)
            if slots:
                our_slot = slots.most_common(1)[0][0]
    if our_slot is None:
        warnings.append("Our draft slot is unknown; recommending best available only.")
    our_roster_id = None if our_slot is None else s2r.get(our_slot, our_slot)

    def is_ours(pick: Mapping[str, Any]) -> bool:
        picked_by = _str_or_none(pick.get("picked_by"))
        if user_id and picked_by == user_id:
            return True
        if user_id and picked_by:
            return False
        if our_slot is None:
            return False
        roster = _int(pick.get("roster_id"))
        if roster is not None:
            return roster == our_roster_id
        return _int(pick.get("draft_slot")) == our_slot

    # players
    all_players = build_players(client.players(), client.projections(league.season))
    drafted_ids = frozenset(str(p["player_id"]) for p in picks if _str_or_none(p.get("player_id")))
    roster: list[SleeperPlayer] = []
    for pick in picks:
        pid = _str_or_none(pick.get("player_id"))
        if not pid or not is_ours(pick):
            continue
        player = all_players.get(pid)
        if player is None:
            meta = pick.get("metadata") or {}
            player = SleeperPlayer(
                player_id=pid,
                name=_name(meta.get("first_name"), meta.get("last_name")) or f"Player {pid}",
                positions=_ordered_positions([meta.get("position")]),
                nba_team=_str_or_none(meta.get("team")),
                injury_status=None,
                stats={},
                adp=None,
                has_projection=False,
            )
        roster.append(player)
    available = tuple(
        p
        for p in all_players.values()
        if p.player_id not in drafted_ids and (p.has_projection or p.adp is not None)
    )

    total = league.teams * league.rounds
    last_pick = max((_int(p["pick_no"]) for p in picks), default=0)
    complete = league.status == "complete" or (total > 0 and last_pick >= total)
    current_pick = None if complete or total <= 0 else last_pick + 1
    on_clock_slot = None
    next_picks: tuple[int, ...] = ()
    if current_pick is not None:
        owner = _pick_owner_roster(current_pick, league, s2r, traded)
        on_clock_slot = r2s.get(owner, owner)
    if our_roster_id is not None and total > 0:
        owned = roster_pick_numbers(
            our_roster_id, league.teams, league.rounds, league.draft_type,
            league.reversal_round, s2r, traded,
        )
        taken = {_int(p["pick_no"]) for p in picks}
        next_picks = tuple(n for n in owned if n not in taken and (current_pick is None or n >= current_pick))

    return DraftState(
        league=league,
        our_slot=our_slot,
        current_pick=current_pick,
        on_clock_slot=on_clock_slot,
        picks=picks,
        drafted_ids=drafted_ids,
        our_roster=tuple(roster),
        available=available,
        our_next_picks=next_picks,
        warnings=tuple(warnings),
    )
