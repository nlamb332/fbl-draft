# ESPN advisor bookmarklet

Use the **× (Close advisor)** button in the advisor header to dismiss the panel and stop observation. It is available in both expanded and collapsed views. Click the FBL Advisor bookmark again to reopen the panel.

Start the local panel with the page runner enabled:

```text
python -m draft_assistant --db runtime/control.sqlite3 serve --page-runner
```

Open `http://127.0.0.1:8765/`. In the **Install the ESPN advisor bookmarklet** card, drag the link labeled **FBL Advisor — drag THIS link to bookmarks** to Chrome's bookmarks bar. The word **THIS** identifies the link to drag. Do not click the browser star while the localhost panel is open: that saves the panel page, not the advisor bookmarklet. Clicking the link in the panel is intentionally intercepted and only shows these installation instructions.

On first launch, paste your live league's ESPN team URL into **Source ESPN team
URL** and click **Save source team**. Open a practice or real draft for that
team and click the bookmark to read its visible team list. Return to the local
panel, select your team, and click **Save setup**. Click the bookmark again to
connect the advisor. Discovery alone never creates a draft session.

To correct setup or select a renamed team, click **Edit setup** in the local
panel. This disconnects existing sessions. Confirm the prefilled source URL
with **Save source team**, read the current team list with the bookmark, and
save the correct selection. New saved scans use league, season, and team IDs
and survive name changes. Scans from the older format need one fresh capture
because they did not record the source league ID.

If dragging does not work, click **Copy bookmarklet URL**. If Chrome blocks clipboard access, the read-only URL field is selected so `Ctrl+C` copies it. You can also right-click the **FBL Advisor** link, choose **Copy link address**, then right-click the bookmarks bar, choose **Add page**, name it `FBL Advisor`, and paste the copied text into the bookmark's **URL** field. Keep the `javascript:` prefix at the start of the URL; without it Chrome will save a normal localhost link.

Open the ESPN Fantasy Basketball practice or real draft room, wait for its draft heading and fourteen-team roster list to render, and click the bookmark. It opens a small localhost bootstrap window, obtains the current panel token and page bundle hash for that invocation, and mounts the compact recommendation-only advisor in the ESPN tab. The bootstrap window closes automatically.

Practice rooms are explicitly attested from their visible practice heading. A real room is accepted only when its league ID matches the configured live league. Scan/Rescan waits for a practice draft to start, pauses it, then scans; in a real draft it scans immediately without pausing.

The bookmarklet accepts only `https://fantasy.espn.com` and `https://www.espn.com` basketball draft pages. It reports a blocked popup, a stopped panel, a missing `--page-runner` flag, an invalid room, or a stale connection in an alert. Start a fresh invocation after restarting the local server so the bookmarklet receives the new token and bundle hash. If the browser asks for local-network access, allow it for this workflow and retry. An ESPN Content Security Policy that blocks localhost scripts can prevent bookmarklet activation; popup or local-network permissions do not override that policy. The alert reports script-load failures.

The localhost bridge is a top-level page rather than a cross-origin token endpoint. The server does not add CORS to `/api/csrf`; the bridge sends its nonce-bound bootstrap message only to the validated ESPN origin that opened it. The observer token returned by `connect-advisor` remains limited to status and recommendation preview, and the bookmarklet never invokes Resume, Draft, queue, ownership, or action operations.

Automated coverage checks the generated JavaScript, the popup handshake, and the real local HTTP routes with synthetic browser fixtures. It does not claim a live ESPN browser run.
