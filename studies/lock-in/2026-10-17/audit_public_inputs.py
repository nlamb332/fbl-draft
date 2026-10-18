import hashlib, json, urllib.request
from datetime import datetime, timezone
from pathlib import Path
out = Path('runtime/lock_in_inputs')
out.mkdir(parents=True, exist_ok=True)
audit = {'fetched_at_utc': datetime.now(timezone.utc).isoformat(), 'seasons': {}}
for season in (2026, 2027):
    url = f'https://api.sleeper.app/projections/nba/{season}?season_type=regular&position[]=PG&position[]=SG&position[]=SF&position[]=PF&position[]=C'
    request = urllib.request.Request(url, headers={'User-Agent': 'FBL-Lock-In-Research/1.0', 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read()
        status = response.status
        mime = response.headers.get('Content-Type')
    rows = json.loads(body)
    if not isinstance(rows, list):
        raise ValueError('Expected projection list')
    (out / f'public_projections_{season}.json').write_bytes(body)
    stats = [row.get('stats') or {} for row in rows]
    core = ('pts','reb','ast','stl','blk','to','tpm')
    fields = core + ('dd','td','tf','ff','bonus_pt_40p','bonus_pt_50p')
    audit['seasons'][str(season)] = {
        'url': url, 'http_status': status, 'content_type': mime, 'response_bytes': len(body),
        'response_sha256': hashlib.sha256(body).hexdigest(), 'rows': len(rows),
        'unique_player_ids': len({row.get('player_id') for row in rows}),
        'complete_core_rows': sum(all(key in values for key in core) for values in stats),
        'field_coverage': {key: sum(key in values for values in stats) for key in fields},
        'nonempty_stats_rows': sum(bool(values) for values in stats),
        'empty_stats_rows': sum(not values for values in stats),
        'declared_seasons': sorted({str(row.get('season')) for row in rows}),
        'companies': sorted({str(row.get('company')) for row in rows}),
        'date_nonnull_rows': sum(row.get('date') is not None for row in rows),
        'week_nonnull_rows': sum(row.get('week') is not None for row in rows),
    }
(out / 'audit.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
print(json.dumps(audit, indent=2))
