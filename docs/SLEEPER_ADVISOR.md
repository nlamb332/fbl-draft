# Sleeper advisor

A second, independent draft advisor for **Sleeper NBA drafts**. It reads the
public Sleeper API, projects fantasy points with your league's own scoring
settings, and recommends the player who adds the most estimated weekly
Lock-In value to your starting lineup. It never makes a pick
for you.

## How it differs from the ESPN advisor

| | ESPN advisor | Sleeper advisor |
| --- | --- | --- |
| Data source | Scraped from the draft room page | Public Sleeper JSON API |
| Bookmarklet / page runner | Required | None |
| Login | Your own browser session | None |
| Access | Observes a browser tab | Read-only; nothing is written to Sleeper |
| Launch | `Launch Advisor.cmd` | `Launch Sleeper Advisor.cmd` or `python -m sleeper_advisor` |

## Launching

On Windows, double-click **`Launch Sleeper Advisor.cmd`**. It asks for your
Sleeper draft URL and Sleeper username (remembered in the ignored file
`runtime/sleeper/config.json` and offered again next time), starts a local
panel on `http://127.0.0.1:8766/`, and opens it in your browser. Keep the window
open while drafting.

From a terminal (Python 3.10+, standard library only):

```
python -m sleeper_advisor once  <draft url or id> --user NAME [--slot N] [--top 10] [--json]
python -m sleeper_advisor watch <draft url or id> --user NAME [--slot N] [--interval 3]
python -m sleeper_advisor serve <draft url or id> --user NAME [--port 8766] [--open-browser]
```

* `once` prints one snapshot (`--json` for machine-readable output).
* `watch` polls every few seconds and reprints when a pick is made. A banner
  shows `>>> YOU ARE ON THE CLOCK <<<`, or `Your pick in N` when you are within
  two picks. Network errors print a warning and polling continues.
* `serve` runs the panel. It binds to `127.0.0.1` only and refuses other hosts.

All commands accept `--cache-dir` (default `runtime/sleeper`).

## Mock drafts versus the real draft

A mock draft and the real league draft have **different draft IDs**. Practise
with the mock URL, but on draft day open the real league draft in Sleeper and
give the advisor *that* URL (`https://sleeper.com/draft/nba/<id>`). The header
shows the draft type (for example `league_mock`) so you can tell which one you
are looking at.

## Your username and slot

The advisor looks up your Sleeper username and finds your slot in the draft
order. Before the order is set Sleeper does not publish it, so:

1. pass `--slot N` (or set it once the order is known) and the advisor uses it;
2. otherwise, if you have already made picks, it infers your slot from them;
3. otherwise it says the slot is unknown and still recommends the best players
   available, assuming the next pick is yours.

Once the draft order exists the order wins over `--slot` (a warning says so).
Traded picks and third-round-reversal drafts are accounted for.

## What the score means

The default policy is `forecast-lock-in-lineup-v1`:

* **Lock-In/wk** estimates one sequentially selected scoring game per player
  each week. More games offer more chances to lock a good score; their points
  are not added together.
* **Added/wk** is the improvement in your best legal starting lineup after
  adding the candidate, measured in estimated weekly Lock-In points.
* Equal lineup gains are ordered by the player's weekly value, then ADP.
  Depth options can have zero immediate lineup gain and still be useful.
* Native PG/SG/G/SF/PF/F/C/UTIL eligibility determines fit. A capacity check
  preserves enough remaining roster spots to fill the starting lineup. This
  is a capacity bound, not a guarantee of future player availability; an
  incomplete eligible player pool generates a warning.
  There are no fixed position quotas or arbitrary bench weights.
* ADP timing is informational; it adds no score premium. Raw FP/game remains
  available separately in JSON. Zero/negative-value players are omitted
  from the displayed best lineup; required slots may therefore appear empty.

The advisor uses the draft's actual team count, roster slots and scoring.
For the six-team profile that means nine starters and five bench players.
Double-double/triple-double and 40+/50+ scoring contributions stack when the
input projections contain both event rates. Missing event rates count as zero
and generate warnings; stacking does not supply an absent projection.

## Forecast inputs and assumptions

Sleeper's public season averages do not include calibrated game variance,
weekly schedules, or health probabilities. Without a local override the
advisor assumes **three games per week, score SD of 20% of the absolute
per-game mean, and 90% availability per game**. These are visible assumptions,
not calibrated predictions. Injury labels are shown but do not set health
probabilities. ADP-only players without an override have zero estimated value.

For each week the model works backwards from the final scheduled game. An
available earlier score can be locked or passed for the expected continuation
value. Final-game DNP scores zero; a previous passed score is not restored.
The draft advisor estimates this option value; it does not operate your locks.

Optional local forecasts are read from `runtime/sleeper/lock_in_forecasts.json`
(or `lock_in_forecasts.json` under `--cache-dir`). All draft commands also
accept `--lock-in-forecasts PATH`. For example, this *illustrative* file uses a
simple points-only league; replace the season, complete scoring map, player
ID and game forecasts with your actual inputs:

```json
{
  "mode": "lock_in",
  "season": "2026",
  "scoring": {"pts": 0.5},
  "source": "illustrative preseason forecasts",
  "players": {
    "example-player-id": {
      "weeks": {
        "1": [
          {"mean": 24, "stddev": 5, "availability_probability": 0.9},
          {"mean": 26, "stddev": 6, "availability_probability": 0.95}
        ]
      }
    }
  }
}
```

Game means and SDs are already in your league's fantasy points. Games must be
in scheduled order; weekly values are averaged over the supplied weeks.
An empty game list represents a week with no games. Unlisted players retain
the labeled default assumptions. The file must match the league season and
complete scoring map exactly. Invalid files pause recommendations rather than
reuse advice with incompatible forecasts. Changed forecast contents, scoring,
roster ownership and refreshed projections trigger recalculation even without
a new pick. Network errors show the last snapshot with an error indicator.

## Evidence and limits

The six-team synthetic study supports exploring forecast Lock-In value and
found no clear gain from added replacement or market-timing premiums. The
live adapter adds exact marginal lineup fit to that metric; it is not the
identical research ranker. The study does not validate production performance
or calibrate the default assumptions. Results and plots remain in
`studies/lock-in/2026-10-17/` on GitHub, with guidance in
`docs/SIX_TEAM_LOCK_IN_STRATEGY.md`.

The production forecast module is self-contained and included in downloadable
ZIPs. Research engines and private runtime forecast/config files are not
required or shipped. Accurate player-specific schedules, variance, health
and rare bonus-event projections still need a supplied forecast source.
