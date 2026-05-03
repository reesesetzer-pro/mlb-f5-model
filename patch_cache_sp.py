"""
Patch today's game_cache.json: fill in missing SP name/hand entries
for pitchers who aren't in sp_df yet (insufficient season IP).
Reads the current schedule from the MLB API and sp_enrichments from
the data_sync log to reconstruct what save_game_cache should have written.
Run once after data_sync.py when SPs show as blank.
"""
import json, requests, time
from datetime import date

MLB_API    = "https://statsapi.mlb.com/api/v1"
SEASON     = 2026
CACHE_PATH = r"C:\F5Model\game_cache.json"

def get_pitcher_hand(mlb_id):
    try:
        r = requests.get(f"{MLB_API}/people/{mlb_id}", timeout=10)
        return r.json()["people"][0]["pitchHand"]["code"]
    except:
        return "R"

def get_recent_form(pitcher_id, n_starts=3):
    """Mirrors data_sync.get_pitcher_recent_form — returns form_score, days_rest."""
    if not pitcher_id:
        return {}
    try:
        r = requests.get(f"{MLB_API}/people/{pitcher_id}/stats",
                         params={"stats": "gameLog", "season": SEASON, "group": "pitching"},
                         timeout=10)
        splits = r.json().get("stats", [{}])[0].get("splits", [])
        starts = [s for s in splits
                  if float(str(s.get("stat", {}).get("inningsPitched", "0")).split(".")[0] or 0) >= 3]
        if not starts:
            return {}
        recent = starts[-n_starts:]

        def era_of(s):
            st = s.get("stat", {})
            ip_str = str(st.get("inningsPitched", "0"))
            try:
                ip = float(ip_str.split(".")[0]) + float(ip_str.split(".")[1]) / 3 if "." in ip_str else float(ip_str)
            except:
                ip = 0
            er = int(st.get("earnedRuns", 0))
            return er / ip * 9 if ip > 0 else None

        recent_eras = [e for e in [era_of(s) for s in recent] if e is not None]
        recent_era = round(sum(recent_eras) / len(recent_eras), 2) if recent_eras else None

        r2 = requests.get(f"{MLB_API}/people/{pitcher_id}/stats",
                          params={"stats": "season", "season": SEASON, "group": "pitching"},
                          timeout=8)
        s2 = r2.json().get("stats", [{}])[0].get("splits", [])
        season_era = float(s2[0]["stat"].get("era", 4.50) or 4.50) if s2 else 4.50

        form_score = round(max(-8, min(8, (season_era - recent_era) * 3)), 1) if recent_era else 0
        try:
            from datetime import datetime
            last_date = datetime.strptime(starts[-1].get("date", ""), "%Y-%m-%d").date()
            days_rest = (date.today() - last_date).days
        except:
            days_rest = 5
        rest_score = -4 if days_rest <= 3 else (2 if days_rest >= 7 else 0)
        time.sleep(0.1)
        return {
            "recent_era":    recent_era,
            "season_era":    season_era,
            "form_score":    round(form_score + rest_score, 1),
            "days_rest":     days_rest,
            "home_away_adj": 0,  # skip split call for patch speed
        }
    except:
        return {}


# ── load schedule to get sp IDs ────────────────────────────────────────────────
today_str = date.today().strftime("%Y-%m-%d")
r = requests.get(
    f"{MLB_API}/schedule?sportId=1&date={today_str}"
    f"&hydrate=probablePitcher,team,venue",
    timeout=15)
schedule = {}
for de in r.json().get("dates", []):
    for g in de.get("games", []):
        pk = g["gamePk"]
        aw = g["teams"]["away"]; hm = g["teams"]["home"]
        schedule[pk] = {
            "away_sp_name": aw.get("probablePitcher", {}).get("fullName", "TBD"),
            "away_sp_id":   aw.get("probablePitcher", {}).get("id"),
            "home_sp_name": hm.get("probablePitcher", {}).get("fullName", "TBD"),
            "home_sp_id":   hm.get("probablePitcher", {}).get("id"),
        }

print(f"Schedule loaded: {len(schedule)} games")

# ── patch cache ────────────────────────────────────────────────────────────────
with open(CACHE_PATH) as f:
    cache = json.load(f)

changed = 0
for game in cache:
    pk = game["game_pk"]
    sched = schedule.get(pk, {})

    for side, name_key, id_key, cache_key in [
        ("away", "away_sp_name", "away_sp_id", "away_sp"),
        ("home", "home_sp_name", "home_sp_id", "home_sp"),
    ]:
        sp_data = game.get(cache_key, {})
        cached_name = sp_data.get("name", "")

        # Only patch entries that are missing name
        if cached_name:
            continue

        sp_name = sched.get(name_key, "TBD")
        sp_id   = sched.get(id_key)
        if not sp_name or sp_name == "TBD" or not sp_id:
            continue

        print(f"  Patching {game['away_team']} @ {game['home_team']} {side} SP: {sp_name} (id={sp_id})")

        hand = get_pitcher_hand(sp_id)
        time.sleep(0.1)
        enrich = get_recent_form(sp_id)
        time.sleep(0.1)

        patched = {
            "name":          sp_name,
            "hand":          hand,
            "xfip":          None,
            "k_bb_pct":      None,
            "hard_hit":      None,
            "barrel_pct":    None,
            "avg_velo":      None,
            "era":           None,
            "sp_score":      None,
            "recent_era":    enrich.get("recent_era"),
            "form_score":    enrich.get("form_score", 0),
            "days_rest":     enrich.get("days_rest"),
            "home_away_adj": enrich.get("home_away_adj", 0),
        }
        # Merge: keep any existing non-None values
        for k, v in patched.items():
            if sp_data.get(k) is None:
                sp_data[k] = v
        game[cache_key] = sp_data
        changed += 1

with open(CACHE_PATH, "w") as f:
    json.dump(cache, f, indent=2)

print(f"\nDone. Patched {changed} SP entries in game_cache.json")
