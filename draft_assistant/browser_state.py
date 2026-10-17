"""Parse and reconcile observations from the visible ESPN draft-room UI.

This module is deliberately transport agnostic.  A caller supplies the text (or
an accessibility/DOM mapping) that it can currently see, together with the
already verified session and room identity.  Nothing here opens a browser,
reads cookies, calls ESPN, or treats an omitted row as a drafted player.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Iterable, Mapping, Sequence

from .contracts import DraftObservation, LeagueConfig, LivePlayer, PickEvent, RoomIdentity, utc_now


UNTRUSTED_FORECAST_FIELDS = ("PF", "TF", "EJ", "DD", "TD", "QD")
PLAYER_COLUMNS = ("FPTS", "GP", "MIN", "FGM", "FGA", "FTM", "FTA", "3PM", "REB", "AST", "STL", "BLK", "TO", "EJ", "PF")
POSITION_NAMES = {"C", "G", "F", "PG", "SG", "SF", "PF"}
_NUMERIC = re.compile(r"^(?:[-+]?\d+(?:\.\d+)?|[-+]?\.\d+)$")
_MISSING = {"", "--", "—", "-", "N/A", "NA", "NULL", "UNKNOWN"}


class BrowserStateError(ValueError):
    """Raised when a visible observation cannot be interpreted safely."""


class ObservationConflict(BrowserStateError):
    """Raised when two observations disagree about immutable room state."""


class _VisibleHTML(HTMLParser):
    """Small HTML-to-visible-text adapter used for copied DOM snapshots."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._hidden = 0
        self._hidden_stack: list[bool] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key.lower(): (value or "") for key, value in attrs}
        hidden_here = tag.lower() in {"script", "style", "noscript", "template", "svg"} or attrs_map.get("aria-hidden", "").lower() == "true"
        self._hidden_stack.append(hidden_here)
        if hidden_here:
            self._hidden += 1
        if tag.lower() in {"br", "tr", "td", "th", "p", "div", "li", "section", "article", "h1", "h2", "h3", "table", "thead", "tbody"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        hidden_here = self._hidden_stack.pop() if self._hidden_stack else tag.lower() in {"script", "style", "noscript", "template", "svg"}
        if hidden_here:
            self._hidden = max(0, self._hidden - 1)
        if tag.lower() in {"br", "tr", "td", "th", "p", "div", "li", "section", "article", "h1", "h2", "h3", "table", "thead", "tbody"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.parts.append(data)


def _visible_lines(snapshot: Any) -> list[str]:
    """Return normalized visible lines without collapsing table row boundaries."""
    if isinstance(snapshot, Mapping):
        for key in ("text", "visible_text", "snapshot", "body", "content"):
            if key in snapshot:
                return _visible_lines(snapshot[key])
        # Structured snapshots are supported by the parser directly.  This
        # fallback gives useful diagnostics rather than silently guessing.
        return []
    if isinstance(snapshot, (bytes, bytearray)):
        snapshot = snapshot.decode("utf-8", "replace")
    if not isinstance(snapshot, str):
        snapshot = "\n".join(str(item) for item in snapshot)
    raw = snapshot
    if "<" in raw and ">" in raw:
        parser = _VisibleHTML()
        try:
            parser.feed(raw)
            raw = "".join(parser.parts)
        except Exception:
            # Plain copied text can contain comparison characters; retain it.
            raw = snapshot
    raw = html.unescape(raw).replace("\r\n", "\n").replace("\r", "\n")
    result: list[str] = []
    for line in raw.split("\n"):
        line = re.sub(r"[ \t\f\v]+", " ", line).strip()
        if line:
            result.append(line)
    return result


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _number(value: Any) -> float | None:
    text = str(value).strip().replace(",", "")
    if text.upper() in _MISSING:
        return None
    if not _NUMERIC.match(text):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _display_number(value: float | None) -> float | int | None:
    if value is None:
        return None
    return int(value) if value.is_integer() else value


def _split_fields(line: str) -> list[str]:
    if "|" in line:
        return [item.strip() for item in line.split("|")]
    if "\t" in line:
        return [item.strip() for item in line.split("\t")]
    return [item for item in re.split(r"\s+", line.strip()) if item]


def _find_numeric_tail(line: str, count: int = len(PLAYER_COLUMNS)) -> tuple[str, list[str]] | None:
    """Split a row into a prefix and its last *count* numeric-ish cells."""
    fields = _split_fields(line)
    if len(fields) >= count + 2 and all(_number(field) is not None or field.strip().upper() in _MISSING for field in fields[-count:]):
        return " ".join(fields[:-count]), fields[-count:]
    # Whitespace snapshots often put the entire player name in one visual run.
    tokens = line.split()
    if len(tokens) < count + 2:
        return None
    tail = tokens[-count:]
    if not all(_number(field) is not None or field.upper() in _MISSING for field in tail):
        return None
    return " ".join(tokens[:-count]), tail


def _parse_positions(prefix: str) -> tuple[str, tuple[str, ...], str | None]:
    """Extract positions/team from a player-row prefix conservatively."""
    prefix = prefix.strip()
    rank_match = re.match(r"^(\d+)\s+(.+)$", prefix)
    if rank_match:
        prefix = rank_match.group(2).strip()
    position_matches = list(re.finditer(r"(?<![A-Za-z])(?:PG|SG|SF|PF|C|G|F)(?![A-Za-z])", prefix.upper()))
    positions: tuple[str, ...] = ()
    if position_matches:
        # Positions are at the end in ESPN's row; ignore team-code lookalikes.
        tail = prefix[position_matches[-1].start():]
        pos_tokens = re.findall(r"PG|SG|SF|PF|C|G|F", tail.upper())
        if pos_tokens and len(pos_tokens) <= 4 and all(token in POSITION_NAMES for token in pos_tokens):
            positions = tuple(dict.fromkeys(pos_tokens))
            prefix = prefix[:position_matches[-1].start()].strip(" |,/\t")
    team = None
    team_match = re.search(r"(?:^|\s)([A-Z]{2,5})$", prefix)
    if team_match and len(prefix[:team_match.start()].strip()) > 2:
        team = team_match.group(1)
        prefix = prefix[:team_match.start()].strip()
    # Injury badges are source state but not player names.
    prefix = re.sub(r"\b(?:DTD|O|IR|Q|SUSP)\b", "", prefix, flags=re.I).strip(" |,/\t")
    return prefix, positions, team


def _player_id(name: str, row_id: str | None = None) -> str:
    if row_id:
        return str(row_id)
    # This is a deterministic visible-row key, not a claim that it is ESPN's
    # private player id.  Operators can require a DOM id before submitting.
    return "visible:" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _unique_players(players: Iterable[LivePlayer]) -> tuple[LivePlayer, ...]:
    result: dict[str, LivePlayer] = {}
    for player in players:
        result.setdefault(player.player_id, player)
    return tuple(result.values())


def _live_player_from_cells(cells: Sequence[str], *, row_id: str | None = None, source: str = "visible_dom") -> LivePlayer | None:
    if len(cells) < len(PLAYER_COLUMNS) + 2:
        return None
    rank = _number(cells[0])
    if rank is None:
        return None
    name = cells[1].strip()
    positions = tuple(item.strip().upper() for item in re.split(r"[,/ ]+", cells[2]) if item.strip().upper() in POSITION_NAMES)
    if not name or not positions:
        return None
    values = dict(zip(PLAYER_COLUMNS, cells[3:3 + len(PLAYER_COLUMNS)]))
    stats: dict[str, float] = {}
    missing = set(UNTRUSTED_FORECAST_FIELDS)
    projected_gp = _number(values.get("GP"))
    for field, raw in values.items():
        value = _number(raw)
        if value is None:
            missing.add(field)
            continue
        # FPTS is the displayed projected total.  GP is a direct seasonal
        # quantity.  The remaining credible columns are per-game inputs.
        if field not in {"GP", *UNTRUSTED_FORECAST_FIELDS}:
            stats[field] = value
    return LivePlayer(
        player_id=_player_id(name, row_id),
        name=name,
        eligibility=positions,
        stats=stats,
        projected_gp=projected_gp,
        draft_rank=rank,
        adp=None,
        nba_team=None,
        source=source,
        missing_stats=tuple(sorted(missing)),
    )


def _parse_pipe_player(line: str, *, source: str = "visible_dom") -> LivePlayer | None:
    cells = _split_fields(line)
    if len(cells) < len(PLAYER_COLUMNS) + 3 or _number(cells[0]) is None:
        return None
    return _live_player_from_cells(cells, source=source)


def _parse_whitespace_player(line: str, *, source: str = "visible_dom") -> LivePlayer | None:
    split = _find_numeric_tail(line)
    if split is None:
        return None
    prefix, tail = split
    rank_match = re.match(r"^(\d+)\s+(.+)$", prefix)
    if not rank_match:
        return None
    name, positions, team = _parse_positions(prefix)
    if not name or not positions:
        return None
    cells = [rank_match.group(1), name, ",".join(positions), *tail]
    player = _live_player_from_cells(cells, source=source)
    if player is None:
        return None
    return replace(player, nba_team=team)


def _table_header_kind(line: str) -> str | None:
    compact = _compact(line)
    if "pickplayerteam" in compact and "projpts" in compact:
        return "history"
    if "rkplayer" in compact and "fpts" in compact and "gp" in compact:
        return "players"
    return None


def _parse_observed_rules(lines: Sequence[str], *, round_number: int | None) -> dict[str, Any]:
    """Extract only rule values actually rendered in the supplied snapshot."""
    text = " ".join(lines)
    rules: dict[str, Any] = {}
    if round_number is not None:
        rules["round"] = round_number
    match = re.search(r"\b(?:teams?|number\s+of\s+teams?)\s*[:=]\s*(\d+)\b", text, re.I)
    if match:
        rules["teams"] = int(match.group(1))
    match = re.search(r"\b(?:rounds?|draft\s+rounds?)\s*[:=]\s*(\d+)\b", text, re.I)
    if match:
        rules["rounds"] = int(match.group(1))
    match = re.search(r"\b(?:starting\s+)?slots?\s*[:=]\s*([A-Z][A-Z0-9,/ +()-]+?)(?:\s{2,}|\bbench\s+slots?\b|\bir\s+slots?\b|$)", text, re.I)
    if match:
        slots = tuple(token.upper() for token in re.findall(r"UTIL|C|G|F", match.group(1).upper()))
        if slots:
            rules["starting_slots"] = slots
            rules["slots"] = slots
    match = re.search(r"\bbench\s+slots?\s*[:=]\s*(\d+)\b", text, re.I)
    if match:
        rules["bench_slots"] = int(match.group(1))
    match = re.search(r"\bir\s+slots?\s*[:=]\s*(\d+)\b", text, re.I)
    if match:
        rules["ir_slots"] = int(match.group(1))
    scoring_match = re.search(r"\bscoring\b[^\n]*", text, re.I)
    if scoring_match:
        scoring: dict[str, float] = {}
        for key, value in re.findall(r"\b([A-Z]{2,3})\s*[:=]\s*(-?\d+(?:\.\d+)?)", scoring_match.group(0).upper()):
            scoring[key] = float(value)
        if scoring:
            rules["scoring"] = scoring
    aliases: dict[str, str] = {}
    if re.search(r"\bTPM\b[^\n]{0,40}\b3PM\b|\b3PM\b[^\n]{0,40}\bTPM\b", text, re.I):
        aliases["TPM"] = "3PM"
    if re.search(r"\bE\b[^\n]{0,40}\bEJ\b|\bEJ\b[^\n]{0,40}\bE\b", text, re.I):
        aliases["E"] = "EJ"
    if aliases:
        rules["stat_aliases"] = aliases
    if re.search(r"\b(?:PF|TF|EJ|DD|TD|QD)\b[^\n]{0,30}(?:not\s+modeled|unknown|unavailable)", text, re.I):
        rules["unmodeled_fields"] = UNTRUSTED_FORECAST_FIELDS
    return rules


def _parse_history_line(line: str) -> PickEvent | None:
    cells = _split_fields(line)
    if len(cells) >= 3 and re.fullmatch(r"(?:Pick\s*)?(\d+)", cells[0], re.I):
        pick = int(re.fullmatch(r"(?:Pick\s*)?(\d+)", cells[0], re.I).group(1))
        if len(cells) >= 6 and _number(cells[-1]) is not None:
            # Pipe rows have pick|player|team|2026PTS|PROJPTS|RK.
            name, team = cells[1], cells[2]
        else:
            name, team = cells[1], cells[2]
        return PickEvent(pick_number=pick, team=team.strip(), player_id=_player_id(name), player_name=name.strip())
    match = re.match(r"^(?:Pick\s*)?(\d+)\s+(.+?)\s{2,}(.+?)(?:\s+[-+]?\d+(?:\.\d+)?){2,}$", line, re.I)
    if match:
        pick, name, team = int(match.group(1)), match.group(2), match.group(3)
        return PickEvent(pick_number=pick, team=team.strip(), player_id=_player_id(name), player_name=name.strip())
    return None


def _parse_history_cells(cells: Sequence[str]) -> PickEvent | None:
    """Parse a history row whose accessible cells arrived on separate lines."""
    if len(cells) < 3 or _number(cells[0]) is None:
        return None
    # A grid row has pick, player, team, then at least the two visible point
    # columns.  Keep the numeric cells opaque; they are not used for identity.
    if len(cells) < 5 or _number(cells[-1]) is None or _number(cells[-2]) is None:
        return None
    name, team = cells[1].strip(), cells[2].strip()
    if not name or not team:
        return None
    return PickEvent(int(float(cells[0])), team, _player_id(name), name)


def _parse_structured(snapshot: Mapping[str, Any], *, session_id: str, room: RoomIdentity, now: str) -> DraftObservation:
    """Parse a connector-friendly structured visible snapshot."""
    history: list[PickEvent] = []
    for raw in snapshot.get("history", ()) or ():
        if isinstance(raw, PickEvent):
            history.append(raw)
        elif isinstance(raw, Mapping):
            name = str(raw.get("player_name", raw.get("name", "")))
            history.append(PickEvent(int(raw["pick_number"]), str(raw.get("team", "")), str(raw.get("player_id", _player_id(name))), name))
    def players(key: str) -> tuple[LivePlayer, ...]:
        result: list[LivePlayer] = []
        for raw in snapshot.get(key, ()) or ():
            if isinstance(raw, LivePlayer):
                result.append(raw)
            elif isinstance(raw, Mapping):
                result.append(LivePlayer.from_dict(raw))
        return tuple(result)
    options = snapshot.get("pick_order", snapshot.get("team_options", snapshot.get("roster_options", ())))
    if isinstance(options, Mapping):
        pick_order = tuple(str(options[key]) for key in sorted(options, key=lambda item: int(item) if str(item).isdigit() else str(item)))
    else:
        pick_order = tuple(str(item) for item in options or ())
    return DraftObservation(
        session_id=session_id,
        room=room,
        observed_at=str(snapshot.get("observed_at", now)),
        revision=int(snapshot.get("revision", 0)),
        phase=str(snapshot.get("phase", "draft")),
        current_pick=int(snapshot["current_pick"]) if snapshot.get("current_pick") is not None else None,
        on_clock_team=snapshot.get("on_clock_team"),
        seconds_remaining=float(snapshot["seconds_remaining"]) if snapshot.get("seconds_remaining") is not None else None,
        pick_order=pick_order,
        history=tuple(history),
        roster=players("roster"),
        available_players=players("available_players"),
        known_players=players("known_players") or _unique_players((*players("available_players"), *players("roster"))),
        autopick=snapshot.get("autopick"),
        rules=dict(snapshot.get("rules", {}) or {}),
        complete=bool(snapshot.get("complete", False)),
        history_complete=bool(snapshot.get("history_complete", False)),
        available_pool_complete=bool(snapshot.get("available_pool_complete", False)),
        warnings=tuple(str(item) for item in snapshot.get("warnings", ()) or ()),
    )


def parse_visible_snapshot(
    snapshot: str | Mapping[str, Any] | Sequence[str],
    *,
    session_id: str,
    room: RoomIdentity,
    config: LeagueConfig,
    observed_at: str | None = None,
    revision: int = 0,
) -> DraftObservation:
    """Parse a visible DOM/accessibility snapshot into ``DraftObservation``.

    ``session_id`` and ``room`` are required keyword arguments by design.  A
    URL, league, team, or practice flag is never inferred from page text.
    """
    if not session_id:
        raise BrowserStateError("session_id is required")
    if not isinstance(room, RoomIdentity):
        raise BrowserStateError("room must be an explicit RoomIdentity")
    if not isinstance(config, LeagueConfig):
        raise BrowserStateError("an explicit LeagueConfig is required")
    now = observed_at or utc_now()
    if isinstance(snapshot, Mapping) and not any(key in snapshot for key in ("text", "visible_text", "snapshot", "body", "content")):
        return _parse_structured({**snapshot, "revision": snapshot.get("revision", revision)}, session_id=session_id, room=room, now=now)
    lines = _visible_lines(snapshot)
    warnings: list[str] = []
    joined = " ".join(lines)
    round_match = re.search(r"\bRND\s*(\d+)\s*of\s*(\d+)\b", joined, re.I)
    round_number = int(round_match.group(1)) if round_match else None
    rounds = int(round_match.group(2)) if round_match else config.rounds
    on_clock_index = next((index for index, line in enumerate(lines) if re.search(r"on\s+the\s+clock", line, re.I)), None)
    on_clock_line = lines[on_clock_index] if on_clock_index is not None else ""
    clock_context = " ".join(lines[on_clock_index : min(len(lines), on_clock_index + 4)]) if on_clock_index is not None else ""
    pick_match = re.search(r"(?:pick\s*#?\s*)(\d+)", clock_context, re.I)
    if pick_match is None:
        pick_match = re.search(r"\bon\s*clock[^\d]*(\d+)", clock_context, re.I)
    current_pick = int(pick_match.group(1)) if pick_match else None
    if current_pick is None:
        warnings.append("current_pick_not_visible")
    # Prefer mm:ss before the generic digit form so ``Clock 00:30`` is 30 s,
    # not the first ``00`` token.
    clock_match = re.search(r"\b(\d{1,2}):(\d{2})\b", joined)
    seconds_remaining = None
    if clock_match:
        seconds_remaining = float(int(clock_match.group(1)) * 60 + int(clock_match.group(2)))
    else:
        clock_match = re.search(r"(?:clock|remaining|left|time)[^\d]*(\d{1,3}(?:\.\d+)?)\s*(?:seconds?|sec|s)?", joined, re.I)
        if clock_match:
            seconds_remaining = float(clock_match.group(1))
    on_clock_team = None
    if on_clock_line:
        remainder = re.sub(r"^.*?on\s+the\s+clock\s*:?\s*", "", clock_context, flags=re.I)
        remainder = re.sub(r"\bPick\s*#?\s*\d+\b", "", remainder, flags=re.I)
        # Accessible DOM often renders the team as the next text node after a
        # standalone ``On the Clock: Pick 13`` label.  Stop at the next known
        # section heading and use the first remaining non-generic token.
        remainder = re.split(r"\b(?:roster|upcoming|controls|players|draft|pick\s+history|board|rules)\b", remainder, maxsplit=1, flags=re.I)[0]
        remainder = remainder.strip(" :-")
        if remainder:
            on_clock_team = config.our_team if _compact(remainder) == _compact(config.our_team) else remainder
    autopick: bool | None = None
    if re.search(r"\bauto\s*[- ]?pick\s*(?:toggle|option|checkbox)?\s*[:=-]?\s*(?:off|disabled|false|unchecked)\b|\bmanual\s*(?:pick|selection)\b", joined, re.I):
        autopick = False
    elif re.search(r"\b(?:you\s*['’]?re|team\s+is)\s+on\s+auto\s*[- ]?pick\b|\bauto\s*[- ]?pick\s*(?:toggle|option|checkbox)?\s*[:=-]?\s*(?:on|enabled|true|checked)\b", joined, re.I):
        autopick = True

    history: list[PickEvent] = []
    available: list[LivePlayer] = []
    roster_rows: list[tuple[str, tuple[str, ...], tuple[str, ...], str | None]] = []
    player_mode: str | None = None
    history_header = False
    player_header = False
    history_complete = False
    available_complete = False
    seat_order: dict[int, str] = {}
    roster_options: list[str] = []
    roster_mode = False
    upcoming: list[int] = []
    pending_player_cells: list[str] = []
    pending_history_cells: list[str] = []
    for line in lines:
        if re.search(r"\broster\s+(?:combobox|teams?)\b", line, re.I):
            roster_mode = True
            continue
        if roster_mode and re.match(r"^(?:upcoming|next|controls?|players?|draft|pick\s+history|board|rules|roster)\b", line, re.I):
            roster_mode = False
        kind = _table_header_kind(line)
        if kind == "history":
            player_mode = "history"
            history_header = True
            pending_player_cells.clear()
            continue
        if kind == "players":
            player_mode = "players"
            player_header = True
            pending_history_cells.clear()
            continue
        if re.search(r"\b(?:all|complete)\s+(?:picks|history)\b|showing\s+1\s*[-–]\s*140", line, re.I):
            history_complete = True
        if re.search(r"\b(?:all|complete)\s+(?:players|available)\b", line, re.I):
            available_complete = True
        if re.search(r"no\s*players?\s*available", line, re.I):
            available_complete = False
            warnings.append("players_table_temporarily_empty")
            continue
        upcoming_matches = re.findall(r"(?:upcoming|next)\s+picks?\s*:?\s*([\d, ]+)", line, re.I)
        for text in upcoming_matches:
            upcoming.extend(int(value) for value in re.findall(r"\d+", text))
        # Seat/team rows in the roster combobox are useful only if explicitly
        # visible; no draft order is invented from an absent combobox.
        seat_match = re.match(r"^(?:seat\s*)?(\d{1,2})\s*(?:[:.)-]|\s{2,})(.+)$", line, re.I)
        if seat_match and len(seat_match.group(2)) < 80 and not _number(seat_match.group(2)):
            seat_number = int(seat_match.group(1))
            if 1 <= seat_number <= config.teams and player_mode is None:
                seat_order[seat_number] = seat_match.group(2).strip()
                continue
        if roster_mode and player_mode is None and not re.match(r"^(?:seat\s*\d+|upcoming|next)\b", line, re.I):
            roster_options.append(line.strip())
        if player_mode == "history":
            item = _parse_history_line(line)
            if item is not None:
                history.append(item)
                continue
            if pending_history_cells:
                pending_history_cells.append(line)
                item = _parse_history_cells(pending_history_cells)
                if item is not None:
                    history.append(item)
                    pending_history_cells.clear()
                continue
            if re.fullmatch(r"\d+", line):
                pending_history_cells[:] = [line]
                continue
        elif player_mode == "players":
            item = _parse_pipe_player(line) if "|" in line or "\t" in line else _parse_whitespace_player(line)
            if item is not None:
                available.append(item)
                continue
            if pending_player_cells:
                pending_player_cells.append(line)
                item = _live_player_from_cells(pending_player_cells)
                if item is not None:
                    available.append(item)
                    pending_player_cells.clear()
                continue
            if re.fullmatch(r"\d+", line):
                pending_player_cells[:] = [line]
                continue
        # An explicit roster list can contain visible names, but a row from the
        # available table must never be promoted into the roster.
        roster_match = re.match(r"^(?:roster|drafted|players?)\s*:\s*(.+)$", line, re.I)
        if roster_match:
            for item in re.split(r"[,;]", roster_match.group(1)):
                item = item.strip()
                if not item:
                    continue
                # Roster cells may expose slot/name or position|name|id.
                parts = [part.strip() for part in item.split("|")]
                slot_match = re.match(r"^(C|G|F|UTIL|BE)\s*[:=-]\s*(.+)$", item, re.I)
                if slot_match:
                    slot, name = slot_match.group(1).upper(), slot_match.group(2).strip()
                    if slot not in {"C", "G", "F"}:
                        warnings.append("roster_row_position_not_visible")
                        continue
                    eligibility = (slot,)
                    roster_rows.append((name, eligibility, tuple(parts[2:3]), None))
                elif len(parts) >= 2 and parts[0].upper() in POSITION_NAMES:
                    roster_rows.append((parts[1], (parts[0].upper(),), tuple(parts[2:3]), None))
                else:
                    warnings.append("roster_row_position_not_visible")

    # History rows are authoritative for the visible roster when they identify
    # our explicit team.  This does not imply that unseen picks are absent.
    our_compact = _compact(config.our_team)
    roster_events = [event for event in history if _compact(event.team) == our_compact]
    roster: list[LivePlayer] = [
        LivePlayer(
            player_id=_player_id(name, ids[0] if ids else None),
            name=name,
            eligibility=eligibility,
            source="visible_roster",
        )
        for name, eligibility, ids, _ in roster_rows
    ]
    # A pick-history row alone is not a roster read-back: its eligibility is
    # absent, so it remains in history and is intentionally not guessed here.
    if roster_events and not roster_rows:
        warnings.append("roster_positions_not_visible")
    if history_header and not history:
        warnings.append("history_rows_not_visible")
    if player_header and not available and "players_table_temporarily_empty" not in warnings:
        warnings.append("available_rows_not_visible")
    if not player_header:
        warnings.append("players_table_not_scoped")
    if any(item.player_id.startswith("visible:") for item in available):
        warnings.append("visible_player_ids_are_row_keys")
    if set(UNTRUSTED_FORECAST_FIELDS).intersection({field for player in available for field in player.missing_stats}):
        warnings.append("unmodeled_stat_fields_preserved_as_missing")
    if len(seat_order) != config.teams and len(roster_options) == config.teams:
        seat_order = {index + 1: name for index, name in enumerate(roster_options)}
    pick_order = tuple(seat_order[index] for index in sorted(seat_order)) if len(seat_order) == config.teams else ()
    frame_complete = bool(current_pick is not None and seconds_remaining is not None and (history_header or player_header))
    if history and current_pick is not None:
        observed_picks = {event.pick_number for event in history}
        if observed_picks == set(range(1, current_pick)):
            history_complete = True
    rules = _parse_observed_rules(lines, round_number=round_number)
    rules.update(
        {
            "upcoming_picks": tuple(dict.fromkeys(upcoming)),
            "draft_rank_field": "RK",
        }
    )
    if re.search(r"practice\s+draft|practice", joined, re.I):
        rules["practice_visible"] = True
    complete = bool(re.search(r"draft\s+(?:complete|finished)|all\s+140\s+picks", joined, re.I) or len(history) >= config.teams * config.rounds)
    catalog = _unique_players((*available, *roster))
    return DraftObservation(
        session_id=session_id,
        room=room,
        observed_at=now,
        revision=revision,
        phase="complete" if complete else ("draft" if current_pick is not None else "pre_draft"),
        current_pick=current_pick,
        on_clock_team=on_clock_team,
        seconds_remaining=seconds_remaining,
        pick_order=pick_order,
        history=tuple(sorted(history, key=lambda event: event.pick_number)),
        roster=tuple(roster),
        available_players=tuple(available),
        known_players=catalog,
        autopick=autopick,
        rules=rules,
        complete=frame_complete,
        history_complete=history_complete or complete,
        available_pool_complete=available_complete,
        warnings=tuple(dict.fromkeys(warnings)),
    )


@dataclass(frozen=True)
class ObservationReconciliation:
    """Result of comparing two explicit observations."""

    observation: DraftObservation
    status: str
    new_picks: tuple[PickEvent, ...] = ()
    warnings: tuple[str, ...] = ()


def reconcile_observations(previous: DraftObservation, current: DraftObservation, *, strict: bool = True) -> ObservationReconciliation:
    """Merge known history while preserving the current visible player window.

    The available-player list is never unioned: absence from a virtualized
    window is not evidence that a player was drafted.  History is unioned only
    for rows that are not contradicted by an explicit complete history.
    """
    if previous.session_id != current.session_id:
        raise ObservationConflict("observation session_id changed")
    if previous.room != current.room:
        raise ObservationConflict("observation room identity changed")
    warnings = list(current.warnings)
    if current.revision and previous.revision and current.revision < previous.revision:
        warnings.append("observation_revision_regressed")
        if strict:
            raise ObservationConflict("observation revision regressed")
    previous_by_pick = {item.pick_number: item for item in previous.history}
    current_by_pick = {item.pick_number: item for item in current.history}
    for pick, event in current_by_pick.items():
        prior = previous_by_pick.get(pick)
        if prior and (prior.player_id != event.player_id or _compact(prior.team) != _compact(event.team)):
            warnings.append(f"history_conflict_pick_{pick}")
            if strict:
                raise ObservationConflict(f"history conflict at pick {pick}")
    merged = dict(previous_by_pick)
    merged.update(current_by_pick)
    if current.history_complete:
        merged = current_by_pick
    new_picks = tuple(event for pick, event in sorted(current_by_pick.items()) if pick not in previous_by_pick)
    if previous.current_pick is not None and current.current_pick is not None and current.current_pick < previous.current_pick and not current.complete:
        warnings.append("current_pick_regressed")
        if strict:
            raise ObservationConflict("current pick regressed")
    status = "unchanged" if not new_picks and current.current_pick == previous.current_pick else "advanced"
    if current.phase == "complete":
        status = "complete"
    catalog = _unique_players((*previous.known_players, *current.known_players, *current.available_players, *current.roster))
    merged_observation = replace(
        current,
        history=tuple(event for _, event in sorted(merged.items())),
        known_players=catalog,
        warnings=tuple(dict.fromkeys(warnings)),
    )
    return ObservationReconciliation(merged_observation, status, new_picks, tuple(dict.fromkeys(warnings)))


def reconcile_observation(previous: DraftObservation, current: DraftObservation, *, strict: bool = True) -> DraftObservation:
    """Convenience API returning only the reconciled observation."""
    return reconcile_observations(previous, current, strict=strict).observation


parse_snapshot = parse_visible_snapshot
parse_dom_snapshot = parse_visible_snapshot
merge_observations = reconcile_observation

__all__ = [
    "BrowserStateError",
    "ObservationConflict",
    "ObservationReconciliation",
    "UNTRUSTED_FORECAST_FIELDS",
    "merge_observations",
    "parse_dom_snapshot",
    "parse_snapshot",
    "parse_visible_snapshot",
    "reconcile_observation",
    "reconcile_observations",
]
