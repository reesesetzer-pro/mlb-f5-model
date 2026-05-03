"""
Standalone script to:
1. Log model picks for today's games (using data_sync logic)
2. Grade any PENDING picks from prior dates using MLB Stats API
3. Display results

Usage:
  python grade_picks_now.py             # log today + grade prior
  python grade_picks_now.py --grade-only  # just grade existing pending picks
"""
import sys, os
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import json
import pandas as pd
from datetime import datetime, date

PICKS_PATH = os.path.join(_HERE, "model_picks.csv")
CACHE_PATH = os.path.join(_HERE, "game_cache.json")
MLB_API    = "https://statsapi.mlb.com/api/v1"

_MP_COLS = ["Date","Game","Team","Side","Market","ML","Book",
            "Model_Prob","Market_Prob","Edge_Pct",
            "Model_Line","Market_Line",
            "SP_Score","LU_Score","Park_Factor","Ump_K",
            "Result","F5_Score"]


def grade_pending_picks():
    """Grade all PENDING picks from prior dates using MLB Stats API."""
    import requests

    if not os.path.exists(PICKS_PATH):
        print(f"[WARN] No picks file at {PICKS_PATH}")
        return

    picks_df = pd.read_csv(PICKS_PATH, dtype={"F5_Score": str})
    if picks_df.empty:
        print("[INFO] Picks file is empty — nothing to grade.")
        return

    pending = picks_df[picks_df["Result"] == "PENDING"]
    if pending.empty:
        print("[INFO] No pending picks to grade.")
        return

    today = date.today()
    changed = 0
    total_pending = len(pending)
    print(f"\n{'='*60}")
    print(f"GRADING {total_pending} PENDING PICKS")
    print(f"{'='*60}\n")

    for pick_date_str, group in pending.groupby("Date"):
        try:
            pick_date = datetime.strptime(pick_date_str, "%m/%d/%Y").date()
        except Exception:
            print(f"[WARN] Cannot parse date: {pick_date_str}")
            continue

        if pick_date > today:
            print(f"[INFO] Skipping {pick_date_str} — future date ({len(group)} picks)")
            continue

        d_str = pick_date.strftime("%Y-%m-%d")
        print(f"\n--- {pick_date_str} ({len(group)} pending picks) ---")

        try:
            r = requests.get(
                f"{MLB_API}/schedule?sportId=1&date={d_str}&hydrate=linescore,team",
                timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"[ERR] API call failed for {d_str}: {e}")
            continue

        scores = {}
        for de in data.get("dates", []):
            for g in de.get("games", []):
                status = g.get("status", {}).get("abstractGameState", "")
                away_name = g["teams"]["away"]["team"]["name"]
                home_name = g["teams"]["home"]["team"]["name"]
                key = f"{away_name} @ {home_name}"

                if status != "Final":
                    print(f"  [SKIP] {key} — status: {status}")
                    continue

                innings = g.get("linescore", {}).get("innings", [])
                f5a = sum((i.get("away") or {}).get("runs", 0) or 0 for i in innings[:5])
                f5h = sum((i.get("home") or {}).get("runs", 0) or 0 for i in innings[:5])
                fi_a = (innings[0].get("away") or {}).get("runs", 0) or 0 if innings else 0
                fi_h = (innings[0].get("home") or {}).get("runs", 0) or 0 if innings else 0
                scores[key] = {
                    "f5_away": f5a, "f5_home": f5h, "f5_total": f5a + f5h,
                    "fi_away": fi_a, "fi_home": fi_h, "fi_total": fi_a + fi_h,
                }
                print(f"  [OK] {key}: F5 {f5a}-{f5h}, 1st Inn {fi_a}-{fi_h}")

        for idx in group.index:
            row    = picks_df.loc[idx]
            game   = str(row.get("Game", ""))
            ls     = scores.get(game)
            if not ls:
                print(f"  [MISS] No score for '{game}' — still PENDING")
                continue

            away, home = (game.split(" @ ", 1) + [""])[:2] if " @ " in game else (game, game)
            market = str(row.get("Market", ""))
            team   = str(row.get("Team", ""))
            side   = str(row.get("Side", ""))
            f5a, f5h = ls["f5_away"], ls["f5_home"]
            result = None

            if market == "F5 ML":
                winner = away if f5a > f5h else (home if f5h > f5a else None)
                if winner is None:               result = "PUSH"
                elif team == winner:             result = "WIN"
                else:                            result = "LOSS"

            elif market == "F5 Spread":
                try:
                    line = float(row.get("Market_Line") or 0)
                    diff = f5a - f5h            # away - home (positive = away winning)
                    # Generic: side covers iff (their score + their line) > opp score.
                    #   Away covers iff diff > -line   (line is the AWAY signed runline)
                    #   Home covers iff diff < line    (line stored is the side's own line;
                    #                                  for home, line = home runline)
                    if away in team:
                        result = "WIN" if diff > -line else ("PUSH" if diff == -line else "LOSS")
                    elif home in team:
                        result = "WIN" if diff < line  else ("PUSH" if diff == line  else "LOSS")
                except Exception:
                    pass

            elif market == "F5 Total":
                try:
                    total = f5a + f5h
                    line = float(row.get("Market_Line") or 0)
                    if "Over"  in side: result = "WIN" if total > line else ("PUSH" if total == line else "LOSS")
                    elif "Under" in side: result = "WIN" if total < line else ("PUSH" if total == line else "LOSS")
                except Exception:
                    pass

            elif market == "NRFI/YRFI":
                fi = ls["fi_total"]
                if   "NRFI" in side: result = "WIN" if fi == 0 else "LOSS"
                elif "YRFI" in side: result = "WIN" if fi  > 0 else "LOSS"

            elif market == "F5 Team Total":
                try:
                    line = float(row.get("Market_Line") or 0)
                    # Determine which team's F5 runs to use
                    is_away = away.split()[-1].lower() in team.lower() or team == away
                    team_runs = f5a if is_away else f5h
                    if "Over" in side:
                        result = "WIN" if team_runs > line else ("PUSH" if team_runs == line else "LOSS")
                    elif "Under" in side:
                        result = "WIN" if team_runs < line else ("PUSH" if team_runs == line else "LOSS")
                except Exception:
                    pass

            if result:
                picks_df.at[idx, "Result"]   = result
                picks_df.at[idx, "F5_Score"] = f"{f5a}-{f5h}"
                print(f"  GRADED [{market}] {team} {side} -> {result}  (F5: {f5a}-{f5h})")
                changed += 1

    if changed:
        picks_df.to_csv(PICKS_PATH, index=False)
        print(f"\n[SAVED] {changed} pick(s) graded -> {PICKS_PATH}")
    else:
        print("\n[INFO] No picks could be graded (games may not be final yet).")

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    total    = len(picks_df)
    settled  = picks_df[picks_df["Result"].isin(["WIN", "LOSS", "PUSH"])]
    wins     = len(settled[settled["Result"] == "WIN"])
    losses   = len(settled[settled["Result"] == "LOSS"])
    pushes   = len(settled[settled["Result"] == "PUSH"])
    still_p  = len(picks_df[picks_df["Result"] == "PENDING"])
    n = wins + losses
    print(f"Total picks:  {total}")
    print(f"Settled:      {len(settled)} (W:{wins} L:{losses} P:{pushes})")
    print(f"Still pending:{still_p}")
    if n > 0:
        print(f"Win rate:     {wins/n*100:.1f}% ({wins}-{losses})")
    print()


def log_picks_today():
    """Run data_sync's log_model_picks for today's games."""
    try:
        from data_sync import log_model_picks, grade_model_picks
        if os.path.exists(CACHE_PATH):
            cache = json.load(open(CACHE_PATH))
            print(f"[INFO] Loaded {len(cache)} games from cache")
            log_model_picks(cache)
        else:
            print(f"[WARN] No game cache at {CACHE_PATH} — run data_sync.py first")
    except Exception as e:
        print(f"[ERR] Could not run log_model_picks: {e}")
        import traceback; traceback.print_exc()


if __name__ == "__main__":
    grade_only = "--grade-only" in sys.argv

    if not grade_only:
        print("Step 1: Logging today's model picks...")
        log_picks_today()
        print()

    print("Step 2: Grading pending picks from prior dates...")
    grade_pending_picks()
