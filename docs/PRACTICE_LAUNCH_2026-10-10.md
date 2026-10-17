# ESPN practice launch evidence — October 10, 2026

The active agent successfully used the signed-in Chrome browser to open the real team page for league 1000000000, season 2027, team 15. It clicked Practice Draft, observed the league-specific setup dialog (Snake, H2H Points, 14 Teams), then clicked Start Practice Draft exactly once. No retry was needed.

ESPN opened a new tab with practice league ID **1000000006**, season 2027, team 15. The heading explicitly identified it as **Practice Draft for Example League**. The room first displayed “Loading your draft”; after waiting it hydrated normally. The fresh ID demonstrates that the earlier practice ID 1000000011 cannot serve as a permanent allowlist.

The room showed fourteen teams: Example Team at seat 13 and thirteen AUTO opponents. It showed ten rounds, first own picks 13 and 16, and the correct C/G/G/F/F/UTIL plus four bench slots. The Rules tab confirmed three IR slots and every custom scoring weight. Its labels TPM and E correspond to the displayed Three Pointers Made and Ejections descriptions.

Three opponent picks occurred before the agent paused the practice via League Manager: Shai Gilgeous-Alexander (Example Opponent 24), Nikola Jokic (Example Opponent 23), and Luka Doncic (Example Opponent 26). The room is paused at pick 4, Team 16. Our roster remains empty.

The practice initially used 30 seconds per pick. The agent selected 90 seconds; the selected native option was read back as value 90000, label 90 seconds. This setting is for this practice only.

While paused, the agent located Victor Wembanyama's exact available-player row and clicked Queue once. The queue subsequently showed rank 4, V. Wembanyama, SA, C. The agent removed that entry and verified “No players in queue.” Immediate snapshots can precede asynchronous updates; read-back must wait for the actual resulting state. Queue insertion/removal is verified; automatic consumption, ordering, and fallback drafting are still unverified.

This is launch and control evidence, not a completed end-to-end draft rehearsal. Direct draft submission, ten guarded selections, final roster legality, Autopick consumption, and observation-to-confirmation latency remain release gates.

The available-player Queue button exposes an explicit DOM `data-player-id` (Victor Wembanyama: 5104157). Use this stable identity rather than a rank or an abbreviated queue name. The player table virtualizes horizontal columns: the current DOM mounted headers only through FTA. A complete custom valuation must capture the remaining columns through supported UI scrolling or use a separately ingested projection source; omitted columns must stay missing. The selected display uses per-game averages for primitive stats and season GP/FPTS.
