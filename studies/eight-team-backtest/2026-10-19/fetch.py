import json, time, urllib.request, datetime, sys
from pathlib import Path
out = Path(sys.argv[1])
def g(u):
    for i in range(3):
        try: return json.loads(urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"fbl-backtest"}),timeout=30).read())
        except Exception as e: err=e; time.sleep(2)
    raise err
def save(name, u):
    p = out/name
    if not p.exists(): p.write_text(json.dumps(g(u)), encoding="utf-8"); time.sleep(0.2)
save("season_2024.json","https://api.sleeper.app/stats/nba/2024?season_type=regular")
save("season_2025.json","https://api.sleeper.app/stats/nba/2025?season_type=regular")
pos="&".join(f"position[]={p}" for p in ("PG","SG","SF","PF","C"))
save("proj_2025.json",f"https://api.sleeper.app/projections/nba/2025?season_type=regular&{pos}")
save("players.json","https://api.sleeper.app/v1/players/nba")
d=datetime.date(2025,10,21); n=0
(out/"dates").mkdir(exist_ok=True)
while d<=datetime.date(2026,4,13):
    name=f"dates/{d.isoformat()}.json"; save(name,f"https://api.sleeper.app/stats/nba/date/{d.isoformat()}?season_type=regular"); d+=datetime.timedelta(days=1); n+=1
print("dates",n)
