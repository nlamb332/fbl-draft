# Sleeper advisor

A second, independent draft advisor for **Sleeper NBA drafts**. It reads the
public Sleeper API, projects fantasy points with your league's own scoring
settings, and recommends the player who adds the most to your starting lineup
given who is likely to still be there at your next pick. It never makes a pick
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

`Score = expected starting-lineup FP/g gained vs. the best player likely to
last until your next pick.`

Scores are relative. The best pick may show `+4.7` while the others are
negative, because every candidate is compared with the best player expected to
survive until you pick again. A negative score does not mean a bad player; it
means you could probably get the better option later. Positions are never
capped or quota'd: position value comes only from how a player fits your
lineup slots (PG, SG, G, SF, PF, F, C, UTIL).

## Known limits

* The 40- and 50-point bonuses and technical/flagrant fouls are not projected;
  they count as zero and appear as warnings.
* Projections are Sleeper/Rotowire per-game numbers with no games-played or
  injury-risk adjustment.
* The model of other teams is ADP-based: it assumes they take players roughly
  in ADP order.
* Players with only an ADP and no stat projection are valued at zero.
