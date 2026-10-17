# Visible operator bridge

The local panel has an Operator bridge form, textarea `#operator-payload`, button **Execute operator command**, and JSON result `#operator-result`. The active browser agent copies its observed ESPN data into the form through normal browser controls. The panel sends it to the local coordinator; the server never operates Chrome.

Commands use `operation`, `session_id`, a unique `request_id`, and operation-specific fields:

| Operation | Additional fields and effect |
|---|---|
| publish | `observation`; publish only |
| start / resume | optional fresh `observation`; explicit lifecycle transition |
| pause | `session_id`, optional `reason`; owner-gated recovery Pause through the local panel form |
| prepare | fresh `observation`; recommend, reserve, claim, consume final guard once; return `command` |
| ack | `action_id`, `status`, optional `detail` / `observation`; record execution outcome |
| reconcile | fresh `observation`; confirm from history plus roster and return status |
| status | inspect current journal and state |

Preparation never starts or resumes a session. An observation supplied to start, resume, prepare, or reconcile must match its session ID. Existing same-origin / CSRF and localhost checks cover this endpoint.

The form disables submission while pending and immediately clears its previous command. Its response echoes `request_id`. Wait until the result contains the new request ID and is no longer pending before reading it. On a request or browser timeout, inspect the journal and ESPN history/roster before retrying; a missing response does not establish that the operation failed.

The browser agent can retain a helper in its CUA session:

```javascript
const operator = async (payload) => {
  const request_id = `trial-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  await panelTab.playwright.locator('#operator-payload').fill(JSON.stringify({...payload, request_id}));
  await panelTab.playwright.getByRole('button', {name:'Execute operator command', exact:true}).click();
  for (let i = 0; i < 5; i++) {
    const result = JSON.parse(await panelTab.playwright.locator('#operator-result').innerText());
    if (result.request_id === request_id && result.status !== 'pending') return result;
  }
  throw new Error('Result still pending; inspect before retry');
};
```

Capture a fresh final frame after locating the candidate, call prepare, and immediately click the exact returned `data-player-id` row's Draft button once. Acknowledge submitted, capture history and roster, then reconcile. Never call another final guard for a command already returned by prepare. If `command` is null or the result is an error, do not click.

A visible-state wait timed out after a successful command in practice; that timeout is not reliable evidence of failure. Read and verify its JSON nonce instead. Prepare and click in the same tool batch, checking observation age and the visible turn again before the click. Never execute a command recovered after a long timeout. Immediately confirm history plus roster before doing projection refresh work.
