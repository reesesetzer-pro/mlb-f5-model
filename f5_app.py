import streamlit as st
import requests
import pandas as pd
import json, os, math
from datetime import datetime, date, timezone, timedelta

# Eastern Time conversion — use zoneinfo (Python 3.9+) for proper DST handling;
# fall back to a fixed seasonal offset if tzdata isn't available on the host.
try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
    def _to_et(dt_utc: datetime) -> datetime:
        return dt_utc.replace(tzinfo=timezone.utc).astimezone(_ET)
except Exception:
    _ET = None
    def _to_et(dt_utc: datetime) -> datetime:
        # MLB season: Mar–Nov = EDT (UTC-4), rest = EST (UTC-5)
        offset = -4 if 3 <= dt_utc.month <= 11 else -5
        return dt_utc.replace(tzinfo=timezone.utc) + timedelta(hours=offset)

st.set_page_config(page_title="MLB F5 Model", page_icon="https://a.espncdn.com/i/teamlogos/leagues/500/mlb.png", layout="wide",
                   initial_sidebar_state="expanded")

# ── CONSTANTS ─────────────────────────────────────────────────────────────────
API_KEY = st.secrets.get("ODDS_API_KEY", "40cfbba84e52cd6da31272d4ac287966")
SPORT   = "baseball_mlb"
BOOKS   = "draftkings,fanduel,betmgm,williamhill_us,espnbet,fanatics,hardrockbet"
REGIONS = "us,us2"
BOOK_LABELS = {
    "draftkings":     "DraftKings",
    "fanduel":        "FanDuel",
    "betmgm":         "BetMGM",
    "williamhill_us": "Caesars",
    "espnbet":        "TheScore",
    "fanatics":       "Fanatics",
    "hardrockbet":    "Hard Rock",
}
REC_BOOKS = {"draftkings","fanduel","betmgm","williamhill_us","espnbet","fanatics","hardrockbet"}
TRACKER_FILE      = "bet_tracker.csv"
SP_FILE           = "sp_data.csv"
CACHE_FILE        = "game_cache.json"
MODEL_PICKS_FILE  = "model_picks.csv"
SNAPSHOT_FILE     = "odds_snapshot.json"
CLV_SNAPSHOT_FILE = "clv_snapshot.json"

# ESPN logo URL builder
def logo_url(abv):
    return f"https://a.espncdn.com/i/teamlogos/mlb/500/{abv.lower()}.png"

# Full MLB abbreviation map
TEAM_ABV = {
    "Arizona Diamondbacks":"ari","Atlanta Braves":"atl","Baltimore Orioles":"bal",
    "Boston Red Sox":"bos","Chicago Cubs":"chc","Chicago White Sox":"cws",
    "Cincinnati Reds":"cin","Cleveland Guardians":"cle","Colorado Rockies":"col",
    "Detroit Tigers":"det","Houston Astros":"hou","Kansas City Royals":"kc",
    "Los Angeles Angels":"laa","Los Angeles Dodgers":"lad","Miami Marlins":"mia",
    "Milwaukee Brewers":"mil","Minnesota Twins":"min","New York Mets":"nym",
    "New York Yankees":"nyy","Oakland Athletics":"oak","Philadelphia Phillies":"phi",
    "Pittsburgh Pirates":"pit","San Diego Padres":"sd","San Francisco Giants":"sf",
    "Seattle Mariners":"sea","St. Louis Cardinals":"stl","Tampa Bay Rays":"tb",
    "Texas Rangers":"tex","Toronto Blue Jays":"tor","Washington Nationals":"wsh",
}

def get_abv(team_name):
    return TEAM_ABV.get(team_name, team_name[:3].lower())

def _last_word(s, fallback=""):
    """Return last word of s, or fallback if s is None/empty."""
    parts = (s or "").split()
    return parts[-1] if parts else fallback

def fmt_time_et(dt):
    """Convert UTC datetime to Eastern and format as 12-hour time."""
    dt_et = _to_et(dt)
    return dt_et.strftime("%I:%M %p").lstrip("0") + " ET"

# Park factors
PARK_FACTORS = {
    "Coors Field":1.28,"Great American Ball Park":1.12,"Globe Life Field":1.08,
    "Fenway Park":1.07,"Wrigley Field":1.06,"Kauffman Stadium":1.04,
    "Angel Stadium":1.03,"American Family Field":1.02,"Guaranteed Rate Field":1.02,
    "Rogers Centre":1.01,"Truist Park":1.01,"Chase Field":1.00,"Camden Yards":1.00,
    "Yankee Stadium":1.00,"Citizens Bank Park":0.99,"Nationals Park":0.99,
    "T-Mobile Park":0.99,"Target Field":0.98,"Dodger Stadium":0.98,
    "Minute Maid Park":0.97,"Busch Stadium":0.97,"LoanDepot Park":0.97,
    "Oracle Park":0.96,"PNC Park":0.96,"Tropicana Field":0.96,"Petco Park":0.95,
    "Progressive Field":0.95,"Oakland Coliseum":0.94,"Comerica Park":0.94,"Citi Field":0.94,
}

def get_park_factor(venue):
    for park, pf in PARK_FACTORS.items():
        if park.lower() in str(venue).lower() or str(venue).lower() in park.lower():
            return pf
    return 1.00

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

  html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

  /* ── Layout ── */
  .block-container { padding-top: 0.75rem !important; max-width: 1400px; }
  .stApp { background: #070d1a; }

  /* ── Sidebar ── */
  section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #090f1e 0%, #0c1628 100%) !important;
    border-right: 1px solid rgba(46,117,182,0.18);
  }
  section[data-testid="stSidebar"] .block-container { padding-top: 1rem !important; }

  /* ── Metrics ── */
  div[data-testid="stMetric"] {
    background: rgba(15,28,58,0.7);
    border: 1px solid rgba(46,117,182,0.22);
    border-radius: 12px;
    padding: 14px 16px;
    backdrop-filter: blur(8px);
  }
  div[data-testid="stMetricValue"] { font-size: 1.65rem !important; font-weight: 700; }
  div[data-testid="stMetricLabel"] { font-size: 0.72rem !important; opacity: 0.7; text-transform: uppercase; letter-spacing: 0.05em; }

  /* ── Divider ── */
  hr { border-color: rgba(46,117,182,0.18) !important; margin: 1rem 0 !important; }

  /* ── Page title ── */
  h1 { font-weight: 800 !important; letter-spacing: -0.02em !important; }
  h2 { font-weight: 700 !important; }

  /* ── Game card ── */
  .game-card {
    background: linear-gradient(145deg, #0c1828, #111d33);
    border-radius: 16px;
    padding: 20px;
    margin-bottom: 14px;
    border: 1px solid rgba(46,117,182,0.22);
    box-shadow: 0 4px 24px rgba(0,0,0,0.35);
  }

  /* ── Signal cards ── */
  .bet-strong {
    background: linear-gradient(145deg, #051510, #091f12);
    border: 1px solid rgba(0,230,118,0.35);
    border-left: 4px solid #00e676;
    border-radius: 14px;
    padding: 18px 20px;
    margin: 10px 0;
    box-shadow: 0 0 28px rgba(0,230,118,0.10);
    animation: pulse-green 3s ease-in-out infinite;
  }
  .bet-moderate {
    background: linear-gradient(145deg, #141000, #1e1800);
    border: 1px solid rgba(255,214,0,0.28);
    border-left: 4px solid #ffd600;
    border-radius: 14px;
    padding: 18px 20px;
    margin: 10px 0;
    box-shadow: 0 0 18px rgba(255,214,0,0.07);
  }
  .no-edge {
    background: linear-gradient(145deg, #0d1020, #111526);
    border: 1px solid rgba(100,120,160,0.18);
    border-left: 4px solid #4a5568;
    border-radius: 14px;
    padding: 18px 20px;
    margin: 10px 0;
  }

  /* ── Animations ── */
  @keyframes pulse-green {
    0%,100% { box-shadow: 0 0 28px rgba(0,230,118,0.10); }
    50%      { box-shadow: 0 0 40px rgba(0,230,118,0.22); }
  }
  @keyframes blink {
    0%,100% { opacity:1; }
    50%     { opacity:0.35; }
  }

  /* ── Confidence dot ── */
  .dot-high  { display:inline-block;width:9px;height:9px;border-radius:50%;background:#00e676;
               box-shadow:0 0 8px #00e676;animation:blink 1.5s infinite;margin-right:7px;vertical-align:middle; }
  .dot-solid { display:inline-block;width:9px;height:9px;border-radius:50%;background:#ffd600;
               margin-right:7px;vertical-align:middle; }
  .dot-lean  { display:inline-block;width:9px;height:9px;border-radius:50%;background:#607d8b;
               margin-right:7px;vertical-align:middle; }

  /* ── Metric pills ── */
  .metric-pill {
    background: rgba(15,28,58,0.85);
    border: 1px solid rgba(46,117,182,0.28);
    border-radius: 20px;
    padding: 5px 13px;
    display: inline-block;
    margin: 3px 2px;
    font-size: 0.81rem;
    backdrop-filter: blur(6px);
  }

  /* ── Market badges ── */
  .mkt-ml     { background:rgba(33,150,243,0.18); border:1px solid rgba(33,150,243,0.45);
                border-radius:7px; padding:3px 10px; font-size:0.74rem; color:#64b5f6; font-weight:700; }
  .mkt-spread { background:rgba(156,39,176,0.18); border:1px solid rgba(156,39,176,0.45);
                border-radius:7px; padding:3px 10px; font-size:0.74rem; color:#ce93d8; font-weight:700; }
  .mkt-total  { background:rgba(255,152,0,0.18);  border:1px solid rgba(255,152,0,0.45);
                border-radius:7px; padding:3px 10px; font-size:0.74rem; color:#ffb74d; font-weight:700; }
  .mkt-team   { background:rgba(0,188,212,0.18);  border:1px solid rgba(0,188,212,0.45);
                border-radius:7px; padding:3px 10px; font-size:0.74rem; color:#4dd0e1; font-weight:700; }

  /* ── Park / ump badges ── */
  .park-badge { background:rgba(46,63,85,0.7); border:1px solid rgba(70,100,140,0.4);
                border-radius:7px; padding:3px 9px; font-size:0.78rem; }
  .ump-badge  { background:rgba(58,42,74,0.7); border:1px solid rgba(100,70,130,0.4);
                border-radius:7px; padding:3px 9px; font-size:0.78rem; }

  /* ── Confidence bar ── */
  .conf-bar-wrap { height:4px; background:rgba(255,255,255,0.07); border-radius:3px; margin-top:10px; overflow:hidden; }
  .conf-bar-fill { height:100%; border-radius:3px; transition:width 0.4s ease; }

  /* ── TOP PICK ribbon ── */
  .top-pick-ribbon {
    display:inline-block;
    background:linear-gradient(90deg,#00e676,#00bcd4);
    color:#000;
    font-weight:800;
    font-size:0.7rem;
    letter-spacing:0.08em;
    padding:3px 10px;
    border-radius:4px;
    text-transform:uppercase;
    margin-left:10px;
    vertical-align:middle;
  }

  /* ── Tables ── */
  .stDataFrame { border-radius: 10px !important; overflow: hidden; }
  [data-testid="stDataFrame"] > div { border-radius: 10px; }

  /* ── Streamlit default overrides ── */
  .stButton > button {
    background: linear-gradient(135deg, #1565c0, #0d47a1);
    border: 1px solid rgba(33,150,243,0.4);
    border-radius: 8px;
    font-weight: 600;
    transition: all 0.2s;
  }
  .stButton > button:hover { transform: translateY(-1px); box-shadow: 0 4px 16px rgba(33,150,243,0.3); }
  .stProgress > div > div { border-radius: 4px !important; }
</style>
""", unsafe_allow_html=True)

# ── HELPERS ───────────────────────────────────────────────────────────────────
def american_to_prob(odds):
    try:
        o = float(odds)
        return 100/(o+100) if o > 0 else -o/(-o+100)
    except: return None

def vig_free(away_ml, home_ml):
    pa = american_to_prob(away_ml)
    ph = american_to_prob(home_ml)
    if not pa or not ph: return None, None
    t = pa + ph
    return pa/t, ph/t

def kelly(edge, odds, bankroll, frac=0.25, max_pct=0.05):
    try:
        b = float(odds)/100 if float(odds)>0 else 100/abs(float(odds))
        p = edge + american_to_prob(odds)
        q = 1 - p
        k = (b*p - q) / b * frac
        return round(min(k*bankroll, bankroll*max_pct), 2)
    except: return 0

def kelly_rounded(edge, odds, bankroll, frac=0.25, max_pct=0.05, step=20):
    """Kelly amount snapped to nearest $20 increment. Min $20 for any real signal."""
    k = kelly(edge, odds, bankroll, frac, max_pct)
    if k <= 0: return 0
    return max(step, round(k / step) * step)

# ── DATA FETCHING ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def fetch_games():
    url = f"https://api.the-odds-api.com/v4/sports/{SPORT}/odds/"
    params = {"apiKey":API_KEY,"regions":"us","markets":"h2h","oddsFormat":"american"}
    try:
        r = requests.get(url, params=params, timeout=15); r.raise_for_status()
        data = r.json(); today = _to_et(datetime.utcnow()).date()
        return [g for g in data
                if _to_et(datetime.strptime(g["commence_time"],"%Y-%m-%dT%H:%M:%SZ")).date()==today], None
    except Exception as e: return [], str(e)

@st.cache_data(ttl=300)
def fetch_f5(event_id, away, home):
    url = f"https://api.the-odds-api.com/v4/sports/{SPORT}/events/{event_id}/odds"
    params = {"apiKey":API_KEY,"regions":REGIONS,
              "markets":"h2h_1st_5_innings,spreads_1st_5_innings,totals_1st_5_innings,team_totals_1st_5_innings,totals_1st_inning,alternate_totals_1st_inning",
              "bookmakers":BOOKS,"oddsFormat":"american"}
    result = {"ml":{}, "spread":{}, "total":{}, "team_total":{}, "fi_total":{}}
    try:
        r = requests.get(url, params=params, timeout=15); r.raise_for_status()
        for bm in r.json().get("bookmakers",[]):
            k = bm["key"]
            for mkt in bm.get("markets",[]):
                mk = mkt["key"]
                if mk == "h2h_1st_5_innings":
                    o = {x["name"]:x["price"] for x in mkt["outcomes"]}
                    result["ml"][k] = {"away":o.get(away),"home":o.get(home)}
                elif mk == "spreads_1st_5_innings":
                    bk_spread = {}
                    for o in mkt["outcomes"]:
                        side = "away" if o["name"]==away else "home" if o["name"]==home else None
                        if side:
                            bk_spread[side] = {"line":o.get("point"), "price":o.get("price")}
                    if bk_spread:
                        result["spread"][k] = bk_spread
                elif mk == "totals_1st_5_innings":
                    bk_tot = {}
                    for o in mkt["outcomes"]:
                        if o["name"]=="Over":   bk_tot["over_line"]  = o.get("point"); bk_tot["over_price"]  = o.get("price")
                        elif o["name"]=="Under": bk_tot["under_price"] = o.get("price")
                    if bk_tot:
                        result["total"][k] = bk_tot
                elif mk in ("totals_1st_inning", "alternate_totals_1st_inning"):
                    if k not in result["fi_total"]:
                        result["fi_total"][k] = {}
                    for o in mkt["outcomes"]:
                        line  = o.get("point")
                        price = o.get("price")
                        name  = o.get("name","")
                        if line is None: continue
                        if abs(line - 0.5) < 0.01:
                            if name == "Under": result["fi_total"][k]["nrfi_price"] = price
                            elif name == "Over": result["fi_total"][k]["yrfi_price"] = price
                        elif abs(line - 1.5) < 0.01:
                            if name == "Under": result["fi_total"][k]["u15_price"] = price
                            elif name == "Over": result["fi_total"][k]["o15_price"] = price
                elif mk == "team_totals_1st_5_innings":
                    for o in mkt["outcomes"]:
                        desc = o.get("description","")
                        direction = o.get("name","")
                        side = "away" if away.lower() in desc.lower() else "home" if home.lower() in desc.lower() else None
                        if side:
                            if k not in result["team_total"]:
                                result["team_total"][k] = {}
                            if side not in result["team_total"][k]:
                                result["team_total"][k][side] = {}
                            result["team_total"][k][side][direction.lower()+"_line"]  = o.get("point")
                            result["team_total"][k][side][direction.lower()+"_price"] = o.get("price")
    except: pass
    return result

@st.cache_data(ttl=60)
def fetch_live_scores():
    """MLB Stats API live/final scores — free, no key needed. Refreshes every 60s."""
    today = _to_et(datetime.utcnow()).strftime("%Y-%m-%d")
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}&hydrate=linescore",
            timeout=10); r.raise_for_status()
        scores = {}
        for de in r.json().get("dates", []):
            for g in de.get("games", []):
                away = g["teams"]["away"]["team"]["name"]
                home = g["teams"]["home"]["team"]["name"]
                status    = g.get("status", {})
                state     = status.get("abstractGameState", "Preview")
                ls        = g.get("linescore", {})
                innings   = ls.get("innings", [])
                f5a = sum((i.get("away") or {}).get("runs", 0) or 0 for i in innings[:5])
                f5h = sum((i.get("home") or {}).get("runs", 0) or 0 for i in innings[:5])
                fi_a = (innings[0].get("away") or {}).get("runs", 0) or 0 if innings else 0
                fi_h = (innings[0].get("home") or {}).get("runs", 0) or 0 if innings else 0
                cur  = ls.get("currentInning", 0)
                half = ls.get("inningHalf", "")
                f5_done = (state == "Final") or (cur > 5) or (cur == 5 and half in ("Bottom","End","Middle"))
                scores[f"{away} @ {home}"] = {
                    "state": state, "detail": status.get("detailedState",""),
                    "inning": cur, "inning_half": half,
                    "away_score": g["teams"]["away"].get("score",0) or 0,
                    "home_score": g["teams"]["home"].get("score",0) or 0,
                    "f5_away": f5a, "f5_home": f5h, "f5_total": f5a+f5h,
                    "fi_away": fi_a, "fi_home": fi_h, "fi_total": fi_a+fi_h,
                    "f5_done": f5_done, "innings_played": len(innings),
                }
        return scores
    except: return {}

@st.cache_data(ttl=300)
def fetch_probable_pitchers():
    """Today's probable starters from MLB Stats API."""
    today = _to_et(datetime.utcnow()).strftime("%Y-%m-%d")
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}&hydrate=probablePitcher",
            timeout=10); r.raise_for_status()
        pitchers = {}
        for de in r.json().get("dates", []):
            for g in de.get("games", []):
                for side in ("away","home"):
                    team = g["teams"][side]["team"]["name"]
                    pp   = g["teams"][side].get("probablePitcher", {})
                    if pp: pitchers[team] = pp.get("fullName","")
        return pitchers
    except: return {}

def load_odds_snapshot():
    today = _to_et(datetime.utcnow()).strftime("%Y-%m-%d")
    if os.path.exists(SNAPSHOT_FILE):
        try:
            with open(SNAPSHOT_FILE) as f: snap = json.load(f)
            if snap.get("date") == today: return snap.get("odds", {})
        except: pass
    return {}

def save_odds_snapshot(odds_dict):
    today = _to_et(datetime.utcnow()).strftime("%Y-%m-%d")
    try:
        with open(SNAPSHOT_FILE,"w") as f: json.dump({"date":today,"odds":odds_dict},f)
    except: pass

def load_clv_snapshot():
    """Load closing-line odds captured at game-time for CLV calculation."""
    today = _to_et(datetime.utcnow()).strftime("%Y-%m-%d")
    if os.path.exists(CLV_SNAPSHOT_FILE):
        try:
            with open(CLV_SNAPSHOT_FILE) as f: snap = json.load(f)
            if snap.get("date") == today: return snap.get("odds", {})
        except: pass
    return {}

def save_clv_snapshot(odds_dict):
    today = _to_et(datetime.utcnow()).strftime("%Y-%m-%d")
    try:
        with open(CLV_SNAPSHOT_FILE,"w") as f: json.dump({"date":today,"odds":odds_dict},f)
    except: pass

def get_line_movement(game_key, book_key, side, current_price, snapshot):
    """Returns (delta, 'better'|'worse'|None). Higher American odds = better for bettor."""
    try:
        opening = snapshot.get(game_key,{}).get(book_key,{}).get(side)
        if opening is None or opening == current_price: return 0, None
        delta = int(current_price) - int(opening)
        return abs(delta), ("better" if delta > 0 else "worse")
    except: return 0, None

def auto_settle_f5(df, live_scores, clv_snap=None):
    """Mark PENDING F5 bets WIN/LOSS/PUSH when the F5 is final. Returns (df, changed)."""
    clv_snap = clv_snap or {}
    changed = False
    for idx, row in df[df["Result"]=="PENDING"].iterrows():
        game = str(row.get("Game",""))
        if " @ " not in game: continue
        ls = live_scores.get(game)
        if not ls or not ls["f5_done"]: continue
        away, home = game.split(" @ ",1)
        market = str(row.get("Market",""))
        side   = str(row.get("Bet_Side",""))
        f5a, f5h = ls["f5_away"], ls["f5_home"]
        result = None
        if market == "F5 ML":
            winner = away if f5a > f5h else (home if f5h > f5a else None)
            if winner is None: result = "PUSH"
            elif winner == away and ("Away" in side or away in side): result = "WIN"
            elif winner == home and ("Home" in side or home in side): result = "WIN"
            else: result = "LOSS"
        elif market == "F5 Total":
            total = f5a + f5h
            try:
                line = float(row.get("Market_Line") or str(side).split()[-1])
                if   "Over"  in side: result = "WIN" if total>line else ("PUSH" if total==line else "LOSS")
                elif "Under" in side: result = "WIN" if total<line else ("PUSH" if total==line else "LOSS")
            except: pass
        elif market == "F5 Spread":
            diff = f5a - f5h
            try:
                line = float(row.get("Market_Line") or 0)
                if   away in side: result = "WIN" if diff>-line else ("PUSH" if diff==-line else "LOSS")
                elif home in side: result = "WIN" if diff<-line else ("PUSH" if diff==-line else "LOSS")
            except: pass
        elif market == "NRFI/YRFI":
            fi = ls["fi_total"]
            if   "NRFI" in side: result = "WIN" if fi==0 else "LOSS"
            elif "YRFI" in side: result = "WIN" if fi>0  else "LOSS"
        elif market == "1st Inn U1.5":
            fi = ls["fi_total"]
            result = "WIN" if fi <= 1 else "LOSS"
        if result:
            df.at[idx,"Result"]  = result
            df.at[idx,"F5_Score"]= f"{f5a}-{f5h}"
            # Auto-fill CLV if closing line was captured at game time
            if not row.get("Closing_ML") and clv_snap.get(game):
                try:
                    bet_ml = float(str(row.get("Bet_ML","")).replace("+",""))
                    # Find best closing line across books
                    closing_prices = []
                    for bk_data in clv_snap[game].values():
                        side_key = "away_ml" if ("Away" in str(row.get("Bet_Side","")) or
                                                  "away" in str(row.get("Bet_Side","")).lower()) else "home_ml"
                        p = bk_data.get(side_key)
                        if p: closing_prices.append(float(p))
                    if closing_prices:
                        closing_ml = round(sum(closing_prices) / len(closing_prices), 0)
                        df.at[idx,"Closing_ML"] = int(closing_ml)
                        df.at[idx,"CLV"]        = round(bet_ml - closing_ml, 1)
                except: pass
            changed = True
    return df, changed

def load_sp_data():
    if os.path.exists(SP_FILE): return pd.read_csv(SP_FILE)
    return pd.DataFrame(columns=["Team","Pitcher","Hand","xFIP","K_BB_pct","Hard_Hit_pct","SP_Score"])

def save_sp_data(df): df.to_csv(SP_FILE, index=False)

def calc_sp_score(xfip, kbb, hh=None, barrel=None, velo=None):
    """
    Multi-factor SP quality (0-100, league avg = 50).
    kbb, hh passed as decimals (0.10, 0.35); barrel as decimal (0.08); velo in mph.
    """
    try:
        comp_xfip   = 50 + (4.20 - xfip)  * 12
        comp_kbb    = 50 + (kbb  - 0.10)  * 200
        comp_hh     = 50 + (0.35 - hh)    * 100 if hh     is not None else 50
        comp_barrel = 50 + (0.08 - barrel) * 300 if barrel is not None else 50
        comp_velo   = 50 + (88.0 - velo)   * 2   if velo   is not None else 50
        if barrel is not None and velo is not None:
            s = comp_xfip*0.32 + comp_kbb*0.28 + comp_hh*0.15 + comp_barrel*0.15 + comp_velo*0.10
        elif hh is not None:
            s = comp_xfip*0.45 + comp_kbb*0.35 + comp_hh*0.20
        else:
            s = comp_xfip*0.60 + comp_kbb*0.40
        return round(max(0, min(100, s)), 1)
    except: return None

def load_tracker():
    if os.path.exists(TRACKER_FILE): return pd.read_csv(TRACKER_FILE)
    return pd.DataFrame(columns=["Date","Game","Bet_Side","Market","Book","Bet_ML",
                                  "Model_Prob","Market_Implied","Edge_Pct","Park_Factor",
                                  "Ump_K_Boost","Away_LU_Score","Home_LU_Score",
                                  "Wager","F5_Score","Result","Profit_Loss","Closing_ML","CLV","Notes"])

def save_tracker(df): df.to_csv(TRACKER_FILE, index=False)

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f: return json.load(f)
    return []

def calc_pnl(row):
    try:
        if row["Result"]=="WIN":
            o=float(row["Bet_ML"]); w=float(row["Wager"])
            return round(w*o/100 if o>0 else w*100/abs(o),2)
        elif row["Result"]=="LOSS": return -float(row["Wager"])
        elif row["Result"]=="PUSH": return 0
    except: return None

# ── MULTI-MARKET MODEL MATH ───────────────────────────────────────────────────
def calc_model_total(away_sp_score, home_sp_score, away_lu, home_lu, pf, ump_k,
                     away_era=None, home_era=None,
                     ump_run_factor=1.0, weather_wind_mult=1.0, weather_temp_mult=1.0):
    """
    Estimate F5 total runs. League avg F5 ≈ 4.5 runs.
    Incorporates SP score, lineup quality, park factor, ump tendency,
    ERA validation, ump run factor, and weather (wind + temp).
    """
    base   = 4.5
    avg_sp = ((away_sp_score or 50) + (home_sp_score or 50)) / 2
    avg_lu = ((away_lu or 50) + (home_lu or 50)) / 2

    sp_adj  = 1 + (50 - avg_sp) / 100 * 0.42
    lu_adj  = 1 + (avg_lu - 50)  / 100 * 0.28
    ump_adj = 1 - (ump_k or 0) * 0.10

    if away_era and home_era:
        avg_era = (away_era + home_era) / 2
        era_adj = 1 + (avg_era - 4.20) / 100 * 0.15
    else:
        era_adj = 1.0

    return round(base * sp_adj * lu_adj * (pf or 1.0) * ump_adj * era_adj
                 * (ump_run_factor or 1.0)
                 * (weather_wind_mult or 1.0)
                 * (weather_temp_mult or 1.0), 2)

def calc_model_team_totals(model_total, away_lu, home_lu, away_sp_score, home_sp_score):
    """
    Split model total between teams.
    Away scoring = f(away offense quality vs home pitching quality).
    """
    a_off = (away_lu or 50); h_off = (home_lu or 50)
    h_pit = (home_sp_score or 50); a_pit = (away_sp_score or 50)
    # Away scores against home pitcher, weighted by offense quality
    away_w = 0.5 + (a_off - h_pit) / 500 + (h_off - a_pit) / 500 * -1
    away_w = max(0.32, min(0.68, away_w))
    return round(model_total * away_w, 2), round(model_total * (1 - away_w), 2)

def calc_model_run_diff(model_away_prob, away_sp_score, home_sp_score,
                        away_lu, home_lu, pf):
    """
    Estimate expected run differential (away − home) through 5 innings.
    Uses both win probability and direct component differentials for a
    more stable estimate than just probability conversion.
    """
    # Probability-based estimate: 10% prob gap ≈ 0.5 run differential
    prob_diff = (model_away_prob - 0.5) * 5.0

    # Component-based estimate
    sp_diff  = ((away_sp_score or 50) - (home_sp_score or 50)) / 100 * 1.5
    lu_diff  = ((away_lu or 50) - (home_lu or 50)) / 100 * 0.8
    # Home field small advantage (~0.15 runs/game through 5)
    hfa      = -0.15
    comp_diff = sp_diff + lu_diff + hfa

    # Blend: 60% probability signal, 40% component signal
    return round(0.60 * prob_diff + 0.40 * comp_diff, 3)

_LG_OPS = 0.720  # 2025/26 MLB average OPS

def _nrfi_eff_ops(nrfi_data):
    """
    Compute effective top-3 OPS for NRFI model.
    Blends season OPS with career B/P history — weight scales with PA sample size.
      < 8 combined PA  → season OPS only (too small to trust)
      8–14 PA          → light blend (up to 40% B/P weight)
      15–29 PA         → moderate blend (50% B/P weight)
      30+ PA           → strong blend (65% B/P weight)
    """
    if not nrfi_data:
        return _LG_OPS
    s_ops = nrfi_data.get("season_ops") or _LG_OPS
    v_ops = nrfi_data.get("vs_sp_ops")
    v_pa  = nrfi_data.get("vs_sp_pa", 0) or 0
    if not v_ops or v_pa < 8:
        return s_ops
    if v_pa >= 30:
        bp_weight = 0.65
    elif v_pa >= 15:
        bp_weight = 0.50
    else:
        # Linear ramp 8 PA → 0.15 weight, 14 PA → 0.40 weight
        bp_weight = 0.15 + (v_pa - 8) / 6 * 0.25
    return s_ops * (1 - bp_weight) + v_ops * bp_weight

def calc_nrfi_prob(away_sp_score, home_sp_score, away_lu, home_lu, pf, ump_k,
                   away_nrfi=None, home_nrfi=None):
    """
    Estimate P(NRFI) — no run by either team in the 1st inning.
    Base ≈ 0.52 (market typically prices NRFI at -115 to -140).

    away_nrfi / home_nrfi: dicts from game_cache with keys:
      season_ops   — avg OPS for batters 1-3 this season
      vs_sp_ops    — PA-weighted career OPS vs. opposing SP (None if <8 PA)
      vs_sp_pa     — total combined PA sample
    When present, OPS replaces the generic lineup quality adjustment.
    When absent, falls back to overall lineup score.
    """
    base   = 0.52
    avg_sp = ((away_sp_score or 50) + (home_sp_score or 50)) / 2
    sp_adj =  (avg_sp - 50) / 200        # ±0.10 for elite / poor SP
    ump_adj = (ump_k  or 0) * 0.04       # K-heavy ump → fewer runs
    pf_adj  = -(pf - 1.0)  * 0.25       # hitter-friendly parks hurt NRFI

    # OPS adjustment — top-3 batter quality vs. this specific pitcher
    if away_nrfi or home_nrfi:
        eff_away = _nrfi_eff_ops(away_nrfi)
        eff_home = _nrfi_eff_ops(home_nrfi)
        avg_ops  = (eff_away + eff_home) / 2
        # Each 0.100 above league avg ≈ -3.5% NRFI probability
        ops_adj  = -(avg_ops - _LG_OPS) * 0.35
    else:
        # Fallback: generic lineup quality (0–100 scale)
        avg_lu  = ((away_lu or 50) + (home_lu or 50)) / 2
        ops_adj = -(avg_lu - 50) / 300

    return round(max(0.35, min(0.72, base + sp_adj + ops_adj + ump_adj + pf_adj)), 4)

def calc_fi_u15_prob(away_sp_score, home_sp_score, away_lu, home_lu, pf, ump_k,
                     away_nrfi=None, home_nrfi=None):
    """
    Estimate P(1st inning total ≤ 1.5) — at most 1 combined run.
    Base ≈ 0.76 (U1.5 1st inning typically priced -220 to -280).
    Uses same OPS/B/P blend as calc_nrfi_prob with slightly smaller adjustments.
    """
    base   = 0.76
    avg_sp = ((away_sp_score or 50) + (home_sp_score or 50)) / 2
    sp_adj =  (avg_sp - 50) / 300
    ump_adj = (ump_k  or 0) * 0.03
    pf_adj  = -(pf - 1.0)  * 0.20

    if away_nrfi or home_nrfi:
        eff_away = _nrfi_eff_ops(away_nrfi)
        eff_home = _nrfi_eff_ops(home_nrfi)
        avg_ops  = (eff_away + eff_home) / 2
        ops_adj  = -(avg_ops - _LG_OPS) * 0.25
    else:
        avg_lu  = ((away_lu or 50) + (home_lu or 50)) / 2
        ops_adj = -(avg_lu - 50) / 400

    return round(max(0.60, min(0.90, base + sp_adj + ops_adj + ump_adj + pf_adj)), 4)

def _norm_cdf(x):
    return (1 + math.erf(x / math.sqrt(2))) / 2

def cover_prob(model_diff, spread_line, sigma=2.6):
    """
    P(away covers spread_line) using normal distribution.
    model_diff = expected away run advantage (positive = away wins).
    sigma calibrated to F5 run distribution (~2.6 std dev).
    """
    return round(_norm_cdf((model_diff - spread_line) / sigma), 4)

def over_prob(model_total, line, sigma=2.3):
    """P(total goes over line). sigma calibrated to F5 total distribution."""
    return round(_norm_cdf((model_total - line) / sigma), 4)

# ── MODEL PICK TRACKING & LEARNING ───────────────────────────────────────────
_MP_COLS = ["Date","Game","Team","Side","Market","ML","Book",
            "Model_Prob","Market_Prob","Edge_Pct",
            "Model_Line","Market_Line",
            "SP_Score","LU_Score","Park_Factor","Ump_K",
            "Result","F5_Score"]

def load_model_picks():
    if os.path.exists(MODEL_PICKS_FILE):
        return pd.read_csv(MODEL_PICKS_FILE)
    return pd.DataFrame(columns=_MP_COLS)

def save_model_picks(df):
    df.to_csv(MODEL_PICKS_FILE, index=False)

def auto_log_model_picks(signals, picks_df, min_model_prob=0.60):
    today = date.today().strftime("%m/%d/%Y")
    new_rows = []
    for s in signals:
        if s["model_p"] < min_model_prob:
            continue
        dupe = picks_df[
            (picks_df["Date"] == today) &
            (picks_df["Game"] == s["game"]) &
            (picks_df["Team"] == s["team"]) &
            (picks_df["Market"] == s.get("market","F5 ML"))
        ]
        if not dupe.empty:
            continue
        new_rows.append({
            "Date":        today,
            "Game":        s["game"],
            "Team":        s["team"],
            "Side":        s["side"],
            "Market":      s.get("market","F5 ML"),
            "ML":          s["ml"],
            "Book":        s["book"],
            "Model_Prob":  round(s["model_p"] * 100, 1),
            "Market_Prob": round(s["mkt_p"] * 100, 1),
            "Edge_Pct":    round(s["edge"] * 100, 1),
            "Model_Line":  s.get("model_line",""),
            "Market_Line": s.get("mkt_line",""),
            "SP_Score":    s.get("sp_score",""),
            "LU_Score":    s.get("lu_score",""),
            "Park_Factor": s.get("park_factor",""),
            "Ump_K":       s.get("ump_k",""),
            "Result":      "PENDING",
            "F5_Score":    "",
        })
    if new_rows:
        picks_df = pd.concat([picks_df, pd.DataFrame(new_rows)], ignore_index=True)
        save_model_picks(picks_df)
    return picks_df

def get_calibration_map(picks_df):
    """Return {prob_bucket: actual_win_rate} for buckets with ≥5 settled picks."""
    settled = picks_df[picks_df["Result"].isin(["WIN","LOSS"])].copy()
    if len(settled) < 5:
        return {}
    settled["Prob_Bucket"] = (settled["Model_Prob"] // 5 * 5).astype(int)
    cal = {}
    for bucket, grp in settled.groupby("Prob_Bucket"):
        w = len(grp[grp["Result"]=="WIN"])
        if len(grp) >= 5:
            cal[int(bucket)] = w / len(grp)
    return cal

def calibrate_prob(raw_pct, cal_map):
    """Blend raw model prob with historical accuracy at that confidence tier."""
    if not cal_map:
        return raw_pct
    bucket = int(raw_pct // 5 * 5)
    if bucket in cal_map:
        return round(0.40 * raw_pct + 0.60 * cal_map[bucket] * 100, 1)
    return raw_pct

# ── SIDEBAR ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div style="padding:8px 0 4px 0">
      <div style="font-size:1.45rem;font-weight:800;letter-spacing:-0.02em">⚾ F5 Model</div>
      <div style="font-size:0.72rem;color:#5a8ab4;text-transform:uppercase;letter-spacing:0.08em;margin-top:2px">MLB Analytics</div>
    </div>
    """, unsafe_allow_html=True)
    # ── Navigation (top) ──
    page = st.radio("", [
        "📋 Today's Slate","🎯 Bet Signals","📚 Best Bets","⚾ NRFI","🌅 Morning Report","✏️ SP Input","📈 Bet Tracker","🏟️ Park Factors","📊 Model Performance"])
    st.divider()
    st.caption(f"🕐 {_to_et(datetime.utcnow()).strftime('%I:%M %p')} ET · Season 2026")
    # Show last data sync status
    _status_path = "sync_status.json"
    if os.path.exists(_status_path):
        try:
            with open(_status_path) as _f: _s = json.load(_f)
            _ok_icon = "🟢" if _s.get("ok") else "🔴"
            _ts = _s.get("last_sync","")[:16]
            st.caption(f"{_ok_icon} Data synced: {_ts}")
            if _s.get("games_today"): st.caption(f"📅 {_s['games_today']} games today")
        except: pass
    if st.button("🔄 Refresh Odds", use_container_width=True):
        st.cache_data.clear(); st.rerun()
    st.divider()
    st.markdown("**💰 Bankroll**")
    bankroll   = st.number_input("Bankroll ($)", value=100, step=25, min_value=25, label_visibility="collapsed")
    c1,c2 = st.columns(2)
    with c1: kelly_frac = st.slider("Kelly Frac", 0.1, 1.0, 0.25, 0.05)
    with c2: max_pct    = st.slider("Max Bet %",  1, 10, 5) / 100
    min_edge = st.slider("Min Edge (%)", 0, 10, 3) / 100
    min_conf = st.slider("Min Model Conf (%)", 50, 70, 60) / 100
    st.divider()
    st.markdown("**🔧 Model Weights**")
    w_sp   = st.slider("SP Score",       0.1, 0.8, 0.45, 0.05)
    w_lu   = st.slider("Lineup Quality", 0.1, 0.6, 0.30, 0.05)
    w_park = st.slider("Park Factor",    0.0, 0.3, 0.15, 0.05)
    w_ump  = st.slider("Ump Tendency",   0.0, 0.2, 0.10, 0.05)

# ── LOAD DATA ─────────────────────────────────────────────────────────────────
games, err       = fetch_games()
sp_df            = load_sp_data()
tracker_df       = load_tracker()
cache            = load_cache()
model_picks_df   = load_model_picks()
cal_map          = get_calibration_map(model_picks_df)
live_scores      = fetch_live_scores()
probable_pitchers= fetch_probable_pitchers()
odds_snapshot    = load_odds_snapshot()
clv_snapshot     = load_clv_snapshot()

# Build cache lookup by team name
cache_by_away = {g["away_team"]: g for g in cache}
cache_by_home = {g["home_team"]: g for g in cache}

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: TODAY'S SLATE
# ══════════════════════════════════════════════════════════════════════════════
if page == "📋 Today's Slate":
    st.markdown(f"""
    <div style="background:linear-gradient(135deg,#0c1e42 0%,#0f2a1a 100%);
                border-radius:16px;padding:24px 28px;margin-bottom:20px;
                border:1px solid rgba(46,117,182,0.25);
                box-shadow:0 8px 32px rgba(0,0,0,0.4)">
      <div style="font-size:1.6rem;font-weight:800;letter-spacing:-0.02em">
        📋 Today's F5 Slate
      </div>
      <div style="font-size:0.9rem;color:#6a9cbf;margin-top:4px">
        {date.today().strftime('%A, %B %d, %Y')} &nbsp;·&nbsp; First 5 Innings
      </div>
    </div>
    """, unsafe_allow_html=True)
    if err: st.error(f"API Error: {err}")
    elif not games: st.info("⚾ No games today. Check back on a game day!")
    else:
        st.success(f"✅ {len(games)} games on today's slate")
        _now_utc = datetime.utcnow()
        for game in games:
            away = game["away_team"]; home = game["home_team"]
            abv_away = get_abv(away);  abv_home = get_abv(home)
            odds_data = fetch_f5(game["id"], away, home)
            try:
                dt = datetime.strptime(game["commence_time"],"%Y-%m-%dT%H:%M:%SZ")
                time_et = fmt_time_et(dt)
                game_started = dt <= _now_utc
            except:
                time_et = ""
                game_started = False
            # Live score lookup
            game_key = f"{away} @ {home}"
            ls = live_scores.get(game_key, {})
            if game_started:
                if ls.get("state") == "Final":
                    time_et = f"Final: {ls['away_score']}-{ls['home_score']}"
                elif ls.get("state") == "Live":
                    inn_label = f"{ls.get('inning_half','')[:3]} {ls.get('inning','')}".strip()
                    time_et = f"🔴 {ls['away_score']}-{ls['home_score']}  {inn_label}"
                    if ls.get("f5_done"):
                        time_et += f"  |  F5: {ls['f5_away']}-{ls['f5_home']}"
                else:
                    time_et = "🔴 Live / Final"

            # Get enriched data from cache
            c_data = cache_by_away.get(away, cache_by_home.get(home, {}))
            pf = c_data.get("park_factor", get_park_factor(c_data.get("venue","")))
            ump = c_data.get("ump_name","")
            ump_k = c_data.get("ump_k_boost",0.0)
            lu_confirmed = c_data.get("lineup_confirmed", False)
            away_lu = c_data.get("away_lineup_score")
            home_lu = c_data.get("home_lineup_score")
            away_sp_data = c_data.get("away_sp",{})
            home_sp_data = c_data.get("home_sp",{})

            # SP scratch detection
            cached_away_sp = _last_word(away_sp_data.get("name","")).lower()
            cached_home_sp = _last_word(home_sp_data.get("name","")).lower()
            mlb_away_sp    = _last_word(probable_pitchers.get(away,"")).lower()
            mlb_home_sp    = _last_word(probable_pitchers.get(home,"")).lower()
            away_sp_scratch = bool(mlb_away_sp and cached_away_sp and mlb_away_sp != cached_away_sp)
            home_sp_scratch = bool(mlb_home_sp and cached_home_sp and mlb_home_sp != cached_home_sp)

            with st.container():
                st.markdown('<div class="game-card">', unsafe_allow_html=True)
                # Team logos + header
                c1, c2, c3, c4, c5 = st.columns([2,1,0.8,1,2])
                with c1:
                    st.image(logo_url(abv_away), width=60)
                    st.markdown(f"**{away}**")
                with c2:
                    sp = away_sp_data
                    if sp:
                        sp_name = sp.get('name','TBD')
                        mlb_name = probable_pitchers.get(away,"")
                        if away_sp_scratch:
                            st.markdown(f"⚠️ **SP CHANGE**")
                            st.caption(f"Cache: {sp_name}")
                            st.caption(f"MLB: **{mlb_name}**")
                        else:
                            st.caption(f"🎯 SP: {sp_name}")
                        if sp.get('sp_score'): st.caption(f"Score: **{sp['sp_score']}**")
                        if sp.get('xfip'):     st.caption(f"xFIP: {sp['xfip']}")
                with c3:
                    st.markdown(f"### {time_et}")
                    pf_color = "🔴" if pf>1.04 else "🟡" if pf>1.01 else "🟢" if pf<0.97 else "⚪"
                    st.caption(f"{pf_color} Park: **{pf:.2f}x**")
                    if ump: st.caption(f"🧑‍⚖️ {_last_word(ump)} ({ump_k:+.2f} K)")
                with c4:
                    sp = home_sp_data
                    if sp:
                        sp_name = sp.get('name','TBD')
                        mlb_name = probable_pitchers.get(home,"")
                        if home_sp_scratch:
                            st.markdown(f"⚠️ **SP CHANGE**")
                            st.caption(f"Cache: {sp_name}")
                            st.caption(f"MLB: **{mlb_name}**")
                        else:
                            st.caption(f"🎯 SP: {sp_name}")
                        if sp.get('sp_score'): st.caption(f"Score: **{sp['sp_score']}**")
                        if sp.get('xfip'):     st.caption(f"xFIP: {sp['xfip']}")
                with c5:
                    st.image(logo_url(abv_home), width=60)
                    st.markdown(f"**{home}**")

                st.divider()

                # Lineup quality
                if lu_confirmed:
                    lc1, lc2 = st.columns(2)
                    with lc1:
                        if away_lu: st.progress(away_lu/100, text=f"{away} Lineup Quality: {away_lu:.0f}/100")
                    with lc2:
                        if home_lu: st.progress(home_lu/100, text=f"{home} Lineup Quality: {home_lu:.0f}/100")
                else:
                    st.caption("⏳ Lineups not yet confirmed")

                # F5 odds table
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown(f"**🛫 {away} (Away) F5 ML**")
                    rows = []
                    for bk, bn in BOOK_LABELS.items():
                        if bk in odds_data["ml"] and odds_data["ml"][bk]["away"]:
                            ml = odds_data["ml"][bk]["away"]
                            rows.append({"Book":bn,"F5 ML":f"{'+' if ml>0 else ''}{ml}"})
                    if rows:
                        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
                        bvals = [odds_data["ml"][b]["away"] for b in BOOK_LABELS if b in odds_data["ml"] and odds_data["ml"][b]["away"]]
                        if bvals: st.success(f"Best: **{'+' if max(bvals)>0 else ''}{max(bvals)}**")
                    else: st.caption("Lines not yet posted")
                with col2:
                    st.markdown(f"**🏠 {home} (Home) F5 ML**")
                    rows = []
                    for bk, bn in BOOK_LABELS.items():
                        if bk in odds_data["ml"] and odds_data["ml"][bk]["home"]:
                            ml = odds_data["ml"][bk]["home"]
                            rows.append({"Book":bn,"F5 ML":f"{'+' if ml>0 else ''}{ml}"})
                    if rows:
                        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
                        bvals = [odds_data["ml"][b]["home"] for b in BOOK_LABELS if b in odds_data["ml"] and odds_data["ml"][b]["home"]]
                        if bvals: st.success(f"Best: **{'+' if max(bvals)>0 else ''}{max(bvals)}**")
                    else: st.caption("Lines not yet posted")

                # F5 result banner (when 5 innings are in the books)
                if ls.get("f5_done"):
                    f5a_s, f5h_s = ls["f5_away"], ls["f5_home"]
                    f5_winner = away if f5a_s > f5h_s else (home if f5h_s > f5a_s else "TIE")
                    f5_color  = "#00e676" if f5_winner != "TIE" else "#ffb74d"
                    st.markdown(f"""<div style="background:rgba(0,230,118,0.08);border:1px solid rgba(0,230,118,0.3);
                    border-radius:8px;padding:8px 14px;margin:6px 0;font-weight:700">
                    F5 Result: <span style="color:{f5_color}">{away} {f5a_s} — {home} {f5h_s}
                    {'  ·  Winner: '+f5_winner if f5_winner!='TIE' else '  ·  TIE'}</span></div>""",
                    unsafe_allow_html=True)

                # Totals
                tots = [(BOOK_LABELS.get(b,b), odds_data["total"][b].get("over_line"))
                        for b in BOOK_LABELS
                        if b in odds_data["total"] and odds_data["total"][b].get("over_line") is not None]
                if tots:
                    st.caption("**F5 Totals:** " + " | ".join([f"{bn}: **{t}**" for bn,t in tots]))
                    avg_t = sum(t for _,t in tots) / len(tots)
                    st.caption(f"Consensus F5 Total: **{avg_t:.2f}**")

                st.markdown('</div>', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: BET SIGNALS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🎯 Bet Signals":
    st.markdown("""
    <div style="background:linear-gradient(135deg,#0c1e42 0%,#1a0c2a 100%);
                border-radius:16px;padding:24px 28px;margin-bottom:20px;
                border:1px solid rgba(46,117,182,0.25);
                box-shadow:0 8px 32px rgba(0,0,0,0.4)">
      <div style="font-size:1.6rem;font-weight:800;letter-spacing:-0.02em">
        🎯 Bet Signals
      </div>
      <div style="font-size:0.9rem;color:#6a9cbf;margin-top:4px">
        Ranked by model win probability &nbsp;·&nbsp; ML · Spread · Total · Team Total
      </div>
    </div>
    """, unsafe_allow_html=True)
    if not games: st.info("No games today.")
    else:
        now_utc = datetime.utcnow()
        signals = []
        for game in games:
            # Skip games that have already started — no pre-game signals on live games
            try:
                dt = datetime.strptime(game["commence_time"],"%Y-%m-%dT%H:%M:%SZ")
                if dt <= now_utc:
                    continue
            except: pass

            away = game["away_team"]; home = game["home_team"]
            abv_away = get_abv(away);  abv_home = get_abv(home)
            odds_data = fetch_f5(game["id"], away, home)
            game_key  = f"{away} @ {home}"
            try:
                time_et = fmt_time_et(dt)
            except: time_et=""

            # Seed snapshot with today's opening lines (first time seen)
            if game_key not in odds_snapshot:
                snap_entry = {}
                for bk in REC_BOOKS:
                    bk_ml = odds_data["ml"].get(bk,{})
                    snap_entry[bk] = {
                        "away_ml": bk_ml.get("away"),
                        "home_ml": bk_ml.get("home"),
                    }
                odds_snapshot[game_key] = snap_entry
            _snapshot_dirty = True

            # CLV auto-snapshot: capture odds as "closing line" for games within 10 min of first pitch
            try:
                _secs_to_start = (dt - datetime.utcnow()).total_seconds()
                if 0 < _secs_to_start < 600 and game_key not in clv_snapshot:
                    _clv_entry = {}
                    for bk in REC_BOOKS:
                        bk_ml = odds_data["ml"].get(bk, {})
                        _clv_entry[bk] = {
                            "away_ml": bk_ml.get("away"),
                            "home_ml": bk_ml.get("home"),
                        }
                    clv_snapshot[game_key] = _clv_entry
                    save_clv_snapshot(clv_snapshot)
            except: pass

            c_data  = cache_by_away.get(away, cache_by_home.get(home,{}))
            pf           = c_data.get("park_factor", 1.0)
            ump_k        = c_data.get("ump_k_boost", 0.0)
            ump_run_fac  = c_data.get("ump_run_factor", 1.0)
            ump_zone     = c_data.get("ump_zone_size", 1.0)
            away_lu      = c_data.get("away_lineup_score")
            home_lu      = c_data.get("home_lineup_score")
            away_sp      = c_data.get("away_sp", {})
            home_sp      = c_data.get("home_sp", {})

            # SP scratch detection for signal cards
            _ca_sp = _last_word(away_sp.get("name","")).lower()
            _ch_sp = _last_word(home_sp.get("name","")).lower()
            _ma_sp = _last_word(probable_pitchers.get(away,"")).lower()
            _mh_sp = _last_word(probable_pitchers.get(home,"")).lower()
            away_scratched = bool(_ma_sp and _ca_sp and _ma_sp != _ca_sp)
            home_scratched = bool(_mh_sp and _ch_sp and _mh_sp != _ch_sp)

            # Base SP scores (pre-computed in cache with barrel/velo if available)
            asp = away_sp.get("sp_score") or 50
            hsp = home_sp.get("sp_score") or 50

            # Apply recent form + home/away split adjustments
            away_form_adj = (away_sp.get("form_score", 0) or 0) + (away_sp.get("home_away_adj", 0) or 0)
            home_form_adj = (home_sp.get("form_score", 0) or 0) + (home_sp.get("home_away_adj", 0) or 0)
            eff_asp = round(max(0, min(100, asp + away_form_adj)), 1)
            eff_hsp = round(max(0, min(100, hsp + home_form_adj)), 1)

            # Matchup-adjusted lineup: 55% platoon/H2H blended, 45% overall
            away_matchup     = c_data.get("away_matchup_score")
            home_matchup     = c_data.get("home_matchup_score")
            away_platoon_adv = c_data.get("away_platoon_adv")
            home_platoon_adv = c_data.get("home_platoon_adv")
            eff_away_lu = round(away_matchup*0.55 + (away_lu or 50)*0.45, 1) if away_matchup else (away_lu or 50)
            eff_home_lu = round(home_matchup*0.55 + (home_lu or 50)*0.45, 1) if home_matchup else (home_lu or 50)

            # Bullpen fatigue (last 3 completed games IP)
            away_bp_ip = c_data.get("away_bp_ip_3d")   # float or None
            home_bp_ip = c_data.get("home_bp_ip_3d")

            # Weather
            wx              = c_data.get("weather") or {}
            wx_wind_mult    = wx.get("wind_multiplier", 1.0) if wx and not wx.get("is_dome") else 1.0
            wx_temp_mult    = wx.get("temp_multiplier", 1.0) if wx and not wx.get("is_dome") else 1.0
            wx_wind_speed   = wx.get("wind_speed", 0)
            wx_wind_dir     = wx.get("wind_dir", "")
            wx_temp         = wx.get("temp")
            wx_precip       = wx.get("precip_pct", 0)
            wx_is_dome      = wx.get("is_dome", False)

            # Recreational books — best price (where to actually bet)
            away_mls_rec = [odds_data["ml"][b]["away"] for b in REC_BOOKS
                            if b in odds_data["ml"] and odds_data["ml"][b]["away"]]
            home_mls_rec = [odds_data["ml"][b]["home"] for b in REC_BOOKS
                            if b in odds_data["ml"] and odds_data["ml"][b]["home"]]
            if not away_mls_rec or not home_mls_rec: continue

            best_away_ml = max(away_mls_rec); best_home_ml = max(home_mls_rec)
            best_away_bk = max((b for b in REC_BOOKS if b in odds_data["ml"] and odds_data["ml"][b]["away"]),
                               key=lambda b: odds_data["ml"][b]["away"])
            best_home_bk = max((b for b in REC_BOOKS if b in odds_data["ml"] and odds_data["ml"][b]["home"]),
                               key=lambda b: odds_data["ml"][b]["home"])

            # Reference probability — vig-free from recreational book average
            true_away, true_home = vig_free(
                sum(away_mls_rec)/len(away_mls_rec), sum(home_mls_rec)/len(home_mls_rec))
            if not true_away: continue

            sp_edge   = (eff_asp - eff_hsp) / 100 * w_sp
            lu_edge   = ((eff_away_lu - eff_home_lu) / 100 * w_lu)
            park_edge = (pf - 1.0) * w_park * -1
            away_kbb  = away_sp.get("k_bb_pct") or 10
            home_kbb  = home_sp.get("k_bb_pct") or 10
            ump_edge  = ump_k * ((away_kbb - home_kbb)/100) * w_ump

            model_away = max(0.05, min(0.95, true_away + sp_edge + lu_edge + park_edge + ump_edge))
            model_home = 1 - model_away
            mkt_away = american_to_prob(best_away_ml)
            mkt_home = american_to_prob(best_home_ml)
            game_tag   = f"{away} @ {home}"

            # ── F5 ML signals ────────────────────────────────────────────────
            for side, edge, ml, bk, model_p, mkt_p, sp_s, lu_s, eff_lu, matchup_s, plat_adv, team, abv in [
                ("Away", model_away-mkt_away, best_away_ml, best_away_bk, model_away, mkt_away,
                 asp, away_lu, eff_away_lu, away_matchup, away_platoon_adv, away, abv_away),
                ("Home", model_home-mkt_home, best_home_ml, best_home_bk, model_home, mkt_home,
                 hsp, home_lu, eff_home_lu, home_matchup, home_platoon_adv, home, abv_home),
            ]:
                if model_p >= 0.52:
                    k = kelly_rounded(max(edge,0), ml, bankroll, kelly_frac, max_pct)
                    signals.append({
                        "game":game_tag,"time":time_et,"team":team,"abv":abv,
                        "away_abv":abv_away,"home_abv":abv_home,
                        "away_abv_str":game["away_team_abv"] if "away_team_abv" in game else abv_away.upper(),
                        "home_abv_str":game["home_team_abv"] if "home_team_abv" in game else abv_home.upper(),
                        "side":side,"market":"F5 ML",
                        "edge":edge,"ml":ml,"book":BOOK_LABELS.get(bk,bk),
                        "model_p":model_p,"mkt_p":mkt_p,"kelly":k,
                        "sp_score":sp_s,"lu_score":lu_s,"eff_lu":eff_lu,
                        "matchup_score":matchup_s,"platoon_adv":plat_adv,
                        "opp_hand": home_sp.get("hand","R") if side=="Away" else away_sp.get("hand","R"),
                        "form_score": away_sp.get("form_score",0) if side=="Away" else home_sp.get("form_score",0),
                        "days_rest":  away_sp.get("days_rest") if side=="Away" else home_sp.get("days_rest"),
                        "weather": wx,
                        "park_factor":pf,"ump_k":ump_k,"ump_zone":ump_zone,
                        "model_line":"","mkt_line":"",
                        "sp_scratch": away_scratched if team==away else home_scratched,
                        "game_key": game_key,
                        "away_bp_ip": away_bp_ip, "home_bp_ip": home_bp_ip,
                    })

            # ── F5 Spread signals ─────────────────────────────────────────────
            spread_books = [b for b in BOOK_LABELS if b in odds_data["spread"]]
            if spread_books:
                # Use consensus spread line
                all_away_lines = [odds_data["spread"][b]["away"]["line"]
                                  for b in spread_books if "away" in odds_data["spread"][b] and odds_data["spread"][b]["away"].get("line") is not None]
                if all_away_lines:
                    consensus_spread = sum(all_away_lines)/len(all_away_lines)
                    model_diff = calc_model_run_diff(model_away, asp, hsp, eff_away_lu, eff_home_lu, pf)
                    # Away covers (favorite or dog)
                    model_cover_away = cover_prob(model_diff, consensus_spread)
                    # Best spread odds per side
                    best_spread_bk_away = max(spread_books, key=lambda b: odds_data["spread"][b].get("away",{}).get("price",-200) or -200)
                    best_spread_bk_home = max(spread_books, key=lambda b: odds_data["spread"][b].get("home",{}).get("price",-200) or -200)
                    away_spread_ml = odds_data["spread"][best_spread_bk_away].get("away",{}).get("price") or -110
                    home_spread_ml = odds_data["spread"][best_spread_bk_home].get("home",{}).get("price") or -110
                    mkt_cover_away = american_to_prob(away_spread_ml) or 0.524
                    mkt_cover_home = american_to_prob(home_spread_ml) or 0.524

                    for side, model_p, mkt_p, ml, bk, team, abv, sp_s, lu_s, cover_line in [
                        (f"Away {'+' if consensus_spread>0 else ''}{consensus_spread:.1f}",
                         model_cover_away, mkt_cover_away, away_spread_ml, best_spread_bk_away,
                         away, abv_away, asp, away_lu, consensus_spread),
                        (f"Home {'+' if -consensus_spread>0 else ''}{-consensus_spread:.1f}",
                         1-model_cover_away, mkt_cover_home, home_spread_ml, best_spread_bk_home,
                         home, abv_home, hsp, home_lu, -consensus_spread),
                    ]:
                        edge = model_p - mkt_p
                        if model_p >= 0.52:
                            k = kelly_rounded(max(edge,0), ml, bankroll, kelly_frac, max_pct)
                            signals.append({
                                "game":game_tag,"time":time_et,"team":team,"abv":abv,
                                "away_abv":abv_away,"home_abv":abv_home,
                                "side":side,"market":"F5 Spread",
                                "edge":edge,"ml":ml,"book":BOOK_LABELS.get(bk,bk),
                                "model_p":model_p,"mkt_p":mkt_p,"kelly":k,
                                "sp_score":sp_s,"lu_score":lu_s,
                                "form_score":0,"days_rest":None,"weather":wx,
                                "park_factor":pf,"ump_k":ump_k,"ump_zone":ump_zone,
                                "model_line":round(model_diff,2),"mkt_line":cover_line,
                            })

            # ── F5 Total signals ──────────────────────────────────────────────
            total_books_all = [b for b in BOOK_LABELS if b in odds_data["total"]]
            total_books_rec = [b for b in REC_BOOKS  if b in odds_data["total"]]
            total_books     = total_books_all  # used for line selection only
            if total_books_rec:
                all_lines = [odds_data["total"][b]["over_line"]
                             for b in total_books_rec if odds_data["total"][b].get("over_line") is not None]
                if all_lines:
                    consensus_total = round(sum(all_lines)/len(all_lines), 1)
                    model_t = calc_model_total(eff_asp, eff_hsp, eff_away_lu, eff_home_lu, pf, ump_k,
                                           away_sp.get("era"), home_sp.get("era"),
                                           ump_run_fac, wx_wind_mult, wx_temp_mult)
                    over_p  = over_prob(model_t, consensus_total)
                    under_p = 1 - over_p

                    best_over_bk  = max(total_books_rec, key=lambda b: odds_data["total"][b].get("over_price",-200) or -200)
                    best_under_bk = max(total_books_rec, key=lambda b: odds_data["total"][b].get("under_price",-200) or -200)
                    over_ml  = odds_data["total"][best_over_bk].get("over_price")  or -110
                    under_ml = odds_data["total"][best_under_bk].get("under_price") or -110
                    mkt_over_p  = american_to_prob(over_ml)  or 0.524
                    mkt_under_p = american_to_prob(under_ml) or 0.524

                    for side, model_p, mkt_p, ml, bk, team in [
                        (f"Over {consensus_total}",  over_p,  mkt_over_p,  over_ml,  best_over_bk,  f"{away}/{home}"),
                        (f"Under {consensus_total}", under_p, mkt_under_p, under_ml, best_under_bk, f"{away}/{home}"),
                    ]:
                        edge = model_p - mkt_p
                        if model_p >= 0.52:
                            k = kelly_rounded(max(edge,0), ml, bankroll, kelly_frac, max_pct)
                            signals.append({
                                "game":game_tag,"time":time_et,"team":team,
                                "abv":abv_away,"away_abv":abv_away,"home_abv":abv_home,
                                "side":side,"market":"F5 Total",
                                "edge":edge,"ml":ml,"book":BOOK_LABELS.get(bk,bk),
                                "model_p":model_p,"mkt_p":mkt_p,"kelly":k,
                                "sp_score":(eff_asp+eff_hsp)/2,"lu_score":((away_lu or 50)+(home_lu or 50))/2,
                                "form_score":0,"days_rest":None,"weather":wx,
                                "park_factor":pf,"ump_k":ump_k,"ump_zone":ump_zone,
                                "model_line":model_t,"mkt_line":consensus_total,
                            })

            # ── F5 Team Total signals ─────────────────────────────────────────
            tt_books = [b for b in BOOK_LABELS if b in odds_data["team_total"]]
            model_t = calc_model_total(eff_asp, eff_hsp, eff_away_lu, eff_home_lu, pf, ump_k,
                                       away_sp.get("era"), home_sp.get("era"),
                                       ump_run_fac, wx_wind_mult, wx_temp_mult)
            m_away_tt, m_home_tt = calc_model_team_totals(model_t, eff_away_lu, eff_home_lu, eff_asp, eff_hsp)

            if tt_books:
                for tm, abv, m_tt, sp_s, lu_s in [
                    (away, abv_away, m_away_tt, asp, away_lu),
                    (home, abv_home, m_home_tt, hsp, home_lu),
                ]:
                    tt_side = "away" if tm == away else "home"
                    mkt_lines = [(b, odds_data["team_total"][b][tt_side])
                                 for b in tt_books if tt_side in odds_data["team_total"][b]]
                    if not mkt_lines: continue
                    best_over_bk  = max(mkt_lines, key=lambda x: x[1].get("over_price",-200)  or -200)[0]
                    best_under_bk = max(mkt_lines, key=lambda x: x[1].get("under_price",-200) or -200)[0]
                    all_tt_lines  = [x[1].get("over_line") for x in mkt_lines if x[1].get("over_line") is not None]
                    if not all_tt_lines: continue
                    mkt_tt = sum(all_tt_lines)/len(all_tt_lines)
                    over_ml  = odds_data["team_total"][best_over_bk][tt_side].get("over_price")  or -110
                    under_ml = odds_data["team_total"][best_under_bk][tt_side].get("under_price") or -110
                    ov_p = over_prob(m_tt, mkt_tt, sigma=1.8)
                    un_p = 1 - ov_p
                    for side, model_p, mkt_p, ml, bk in [
                        (f"{tm} Over {mkt_tt}",  ov_p, american_to_prob(over_ml)  or 0.524, over_ml,  best_over_bk),
                        (f"{tm} Under {mkt_tt}", un_p, american_to_prob(under_ml) or 0.524, under_ml, best_under_bk),
                    ]:
                        edge = model_p - mkt_p
                        if model_p >= 0.52:
                            k = kelly_rounded(max(edge,0), ml, bankroll, kelly_frac, max_pct)
                            signals.append({
                                "game":game_tag,"time":time_et,"team":tm,"abv":abv,
                                "away_abv":abv_away,"home_abv":abv_home,
                                "side":side,"market":"F5 Team Total",
                                "edge":edge,"ml":ml,"book":BOOK_LABELS.get(bk,bk),
                                "model_p":model_p,"mkt_p":mkt_p,"kelly":k,
                                "sp_score":sp_s,"lu_score":lu_s,
                                "park_factor":pf,"ump_k":ump_k,"ump_zone":ump_zone,
                                "model_line":m_tt,"mkt_line":mkt_tt,
                            })
            else:
                # No market data — show model estimate only if strongly leaning
                for tm, abv, m_tt, sp_s, lu_s in [
                    (away, abv_away, m_away_tt, asp, away_lu),
                    (home, abv_home, m_home_tt, hsp, home_lu),
                ]:
                    ov_p = over_prob(m_tt, round(m_tt), sigma=1.8)
                    un_p = 1 - ov_p
                    if max(ov_p, un_p) >= 0.57:
                        side = f"{tm} Over ~{m_tt}" if ov_p > un_p else f"{tm} Under ~{m_tt}"
                        model_p = max(ov_p, un_p)
                        signals.append({
                            "game":game_tag,"time":time_et,"team":tm,"abv":abv,
                            "side":side,"market":"F5 Team Total",
                            "edge":0.0,"ml":None,"book":"Model Only",
                            "model_p":model_p,"mkt_p":0.50,"kelly":0,
                            "sp_score":sp_s,"lu_score":lu_s,
                            "park_factor":pf,"ump_k":ump_k,"ump_zone":ump_zone,
                            "model_line":m_tt,"mkt_line":"—",
                        })

            # ── NRFI / YRFI / 1st Inning U1.5 signals ────────────────────────
            fi_data = odds_data.get("fi_total", {})
            fi_books_rec = [b for b in REC_BOOKS if b in fi_data]
            if fi_books_rec:
                # Top-3 OPS + B/P history from cache (populated by data_sync.py)
                away_nrfi = c_data.get("away_nrfi_top3") or {}
                home_nrfi = c_data.get("home_nrfi_top3") or {}

                model_nrfi = calc_nrfi_prob(eff_asp, eff_hsp, eff_away_lu, eff_home_lu,
                                            pf, ump_k, away_nrfi, home_nrfi)
                model_yrfi = round(1 - model_nrfi, 4)
                model_u15  = calc_fi_u15_prob(eff_asp, eff_hsp, eff_away_lu, eff_home_lu,
                                              pf, ump_k, away_nrfi, home_nrfi)
                _fi_base   = {"game":game_tag,"time":time_et,
                              "away_abv":abv_away,"home_abv":abv_home,
                              "form_score":0,"days_rest":None,"weather":wx,
                              "matchup_score":0,"park_factor":pf,
                              "ump_k":ump_k,"ump_zone":ump_zone,
                              "sp_score":(eff_asp+eff_hsp)/2,
                              "lu_score":((away_lu or 50)+(home_lu or 50))/2,
                              "away_nrfi":away_nrfi,"home_nrfi":home_nrfi}

                for label, market, model_p, price_key, team, abv in [
                    ("NRFI", "NRFI/YRFI",    model_nrfi, "nrfi_price", away, abv_away),
                    ("YRFI", "NRFI/YRFI",    model_yrfi, "yrfi_price", away, abv_away),
                    (f"1st Inn U1.5", "1st Inn U1.5", model_u15, "u15_price", away, abv_away),
                ]:
                    prices = [fi_data[b][price_key] for b in fi_books_rec if fi_data[b].get(price_key)]
                    if not prices: continue
                    best_price = max(prices)
                    best_bk    = max(fi_books_rec, key=lambda b: fi_data[b].get(price_key, -9999))
                    mkt_p      = american_to_prob(best_price) or 0.524
                    edge       = model_p - mkt_p
                    if model_p >= 0.52:
                        k = kelly_rounded(max(edge, 0), best_price, bankroll, kelly_frac, max_pct)
                        signals.append({**_fi_base,
                            "team":team,"abv":abv,
                            "side":f"{label} — {away} @ {home}","market":market,
                            "edge":edge,"ml":best_price,
                            "book":BOOK_LABELS.get(best_bk, best_bk),
                            "model_p":model_p,"mkt_p":mkt_p,"kelly":k,
                            "model_line":None,"mkt_line":None,
                        })

        # Persist snapshot (only writes if new games were added today)
        if odds_snapshot:
            save_odds_snapshot(odds_snapshot)

        # ── DISPLAY ───────────────────────────────────────────────────────────
        if not signals:
            st.info("No signals found on today's slate with current data.")
        else:
            # Primary sort: model probability (most likely to hit), secondary: edge
            signals.sort(key=lambda x: (x["model_p"], x["edge"]), reverse=True)
            # Cache for Morning Report tab
            st.session_state["signals_cache"] = signals
            st.session_state["signals_date"]  = str(date.today())

            # Auto-log all signals ≥52% model_p to the learning tracker
            model_picks_df = auto_log_model_picks(signals, model_picks_df)

            # ── Deduplicate: for symmetric markets (Spread, Total, Team Total)
            # keep only the stronger side per game × market × team combination.
            # ML is directional (away vs home) so both sides can legitimately show.
            _seen = {}
            deduped = []
            for s in signals:
                mkt = s.get("market", "F5 ML")
                if mkt == "F5 ML":
                    deduped.append(s)
                else:
                    # Key: game + market + team (for team totals) or game + market (for total/spread)
                    team_key = s["team"] if mkt == "F5 Team Total" else ""
                    key = (s["game"], mkt, team_key)
                    if key not in _seen:
                        _seen[key] = s  # first seen = highest model_p (already sorted)
            deduped += list(_seen.values())
            deduped.sort(key=lambda x: (x["model_p"], x["edge"]), reverse=True)
            signals = deduped

            high_conf = [s for s in signals if s["model_p"] >= 0.60]
            solid     = [s for s in signals if 0.55 <= s["model_p"] < 0.60]

            m1,m2,m3,m4 = st.columns(4)
            m1.metric("Total Signals",       len(signals))
            m2.metric("🔥 High Conf (≥60%)",  len(high_conf))
            m3.metric("🟢 Solid (55-60%)",    len(solid))
            m4.metric("Total Rec. Wagers",   f"${sum(s['kelly'] for s in signals if s['edge']>=min_edge and s['ml']):,.0f}")

            # ── Double of the Day ──────────────────────────────────────────────
            # Any market (ML, Spread, Total, Team Total) is eligible.
            # Prefer legs with odds in the +90 to +220 range for meaningful payout.
            # Fall back to any signal with a real line if no value-range picks exist.
            all_bettable = [s for s in signals if s["ml"] is not None]
            value_legs   = [s for s in all_bettable if 90 <= float(s["ml"]) <= 220]
            parlay_pool  = value_legs if len(value_legs) >= 2 else all_bettable

            # Pick the two highest-confidence legs from different games
            seen_games, parlay_legs = set(), []
            for s in parlay_pool:
                if s["game"] not in seen_games:
                    parlay_legs.append(s)
                    seen_games.add(s["game"])
                if len(parlay_legs) == 2:
                    break

            if len(parlay_legs) == 2:
                leg1, leg2 = parlay_legs[0], parlay_legs[1]
                if True:  # placeholder to keep indentation consistent
                    def to_decimal(american):
                        o = float(american)
                        return (o / 100) + 1 if o > 0 else (100 / abs(o)) + 1

                    def decimal_to_american(dec):
                        if dec >= 2.0:
                            return f"+{int((dec - 1) * 100)}"
                        else:
                            return f"{int(-100 / (dec - 1))}"

                    parlay_prob   = leg1["model_p"] * leg2["model_p"]
                    parlay_dec    = to_decimal(leg1["ml"]) * to_decimal(leg2["ml"])
                    parlay_amr    = decimal_to_american(parlay_dec)
                    parlay_pct    = int(parlay_prob * 100)
                    parlay_payout = round((parlay_dec - 1) * bankroll, 2)
                    parlay_edge   = parlay_prob - (1 / parlay_dec)

                    _is_value = len(value_legs) >= 2
                    _parlay_label = "Value Dog" if _is_value else "Best Available"
                    with st.expander(f"🎰 Double of the Day  ·  {parlay_amr}  ·  {parlay_pct}% Hit Prob  ·  +${parlay_payout:,.0f} on $100", expanded=False):
                        st.markdown(f"""
                        <div style="background:linear-gradient(145deg,#0a1a2e,#0f2040);border-radius:14px;
                                    padding:18px 20px;border:1px solid rgba(33,150,243,0.35);
                                    box-shadow:0 0 28px rgba(33,150,243,0.10)">
                          <div style="font-size:0.78rem;color:#5a8ab4;text-transform:uppercase;
                                      letter-spacing:0.07em;margin-bottom:12px">⚡ 2-LEG {_parlay_label.upper()} PARLAY</div>
                          <div style="display:flex;flex-direction:column;gap:10px">
                            <div style="background:rgba(255,255,255,0.04);border-radius:10px;padding:12px 16px">
                              <div style="font-weight:700;font-size:0.95rem">Leg 1 &nbsp;·&nbsp; {leg1['side']}</div>
                              <div style="color:#7a9cbf;font-size:0.82rem">{leg1['game']} &nbsp;·&nbsp; {leg1['time']}</div>
                              <div style="display:flex;gap:8px;margin-top:6px">
                                <span style="background:rgba(33,150,243,0.15);border:1px solid rgba(33,150,243,0.4);
                                             border-radius:6px;padding:2px 9px;font-size:0.78rem;color:#64b5f6">
                                  {'+' if int(leg1['ml'])>0 else ''}{leg1['ml']} @ {leg1['book']}
                                </span>
                                <span style="background:rgba(0,230,118,0.10);border:1px solid rgba(0,230,118,0.3);
                                             border-radius:6px;padding:2px 9px;font-size:0.78rem;color:#69f0ae">
                                  {int(leg1['model_p']*100)}% model
                                </span>
                              </div>
                            </div>
                            <div style="background:rgba(255,255,255,0.04);border-radius:10px;padding:12px 16px">
                              <div style="font-weight:700;font-size:0.95rem">Leg 2 &nbsp;·&nbsp; {leg2['side']}</div>
                              <div style="color:#7a9cbf;font-size:0.82rem">{leg2['game']} &nbsp;·&nbsp; {leg2['time']}</div>
                              <div style="display:flex;gap:8px;margin-top:6px">
                                <span style="background:rgba(33,150,243,0.15);border:1px solid rgba(33,150,243,0.4);
                                             border-radius:6px;padding:2px 9px;font-size:0.78rem;color:#64b5f6">
                                  {'+' if int(leg2['ml'])>0 else ''}{leg2['ml']} @ {leg2['book']}
                                </span>
                                <span style="background:rgba(0,230,118,0.10);border:1px solid rgba(0,230,118,0.3);
                                             border-radius:6px;padding:2px 9px;font-size:0.78rem;color:#69f0ae">
                                  {int(leg2['model_p']*100)}% model
                                </span>
                              </div>
                            </div>
                          </div>
                          <div style="display:flex;gap:16px;margin-top:14px;padding-top:12px;
                                      border-top:1px solid rgba(255,255,255,0.07)">
                            <div style="text-align:center;flex:1">
                              <div style="font-size:1.4rem;font-weight:800">{parlay_pct}%</div>
                              <div style="font-size:0.68rem;color:#5a8ab4;text-transform:uppercase">Hit Prob</div>
                            </div>
                            <div style="text-align:center;flex:1">
                              <div style="font-size:1.4rem;font-weight:800">{parlay_amr}</div>
                              <div style="font-size:0.68rem;color:#5a8ab4;text-transform:uppercase">Parlay Odds</div>
                            </div>
                            <div style="text-align:center;flex:1">
                              <div style="font-size:1.4rem;font-weight:800;color:#00e676">+${parlay_payout:,.2f}</div>
                              <div style="font-size:0.68rem;color:#5a8ab4;text-transform:uppercase">Win on $100</div>
                            </div>
                            <div style="text-align:center;flex:1">
                              <div style="font-size:1.4rem;font-weight:800;color:{'#00e676' if parlay_edge>0 else '#ff7043'}">{parlay_edge*100:+.1f}%</div>
                              <div style="font-size:0.68rem;color:#5a8ab4;text-transform:uppercase">Edge</div>
                            </div>
                          </div>
                        </div>
                        """, unsafe_allow_html=True)

                        if st.button("📋 Log Parlay to Tracker", key="log_parlay", use_container_width=True):
                            today_str = date.today().strftime("%m/%d/%Y")
                            parlay_note = f"PARLAY: {leg1['side']} ({leg1['ml']}) + {leg2['side']} ({leg2['ml']})"
                            new_row = {
                                "Date": today_str,
                                "Game": f"{leg1['game']} + {leg2['game']}",
                                "Bet_Side": f"Parlay: {leg1['side']} / {leg2['side']}",
                                "Market": "Parlay",
                                "Book": f"{leg1['book']} / {leg2['book']}",
                                "Bet_ML": parlay_amr,
                                "Model_Prob": parlay_pct,
                                "Market_Implied": round(1/parlay_dec*100, 1),
                                "Edge_Pct": round(parlay_edge*100, 1),
                                "Park_Factor": "", "Ump_K_Boost": "",
                                "Away_LU_Score": "", "Home_LU_Score": "",
                                "Wager": bankroll,
                                "F5_Score": "", "Result": "PENDING",
                                "Profit_Loss": "", "Closing_ML": "", "CLV": "",
                                "Notes": parlay_note,
                            }
                            tracker_df = pd.concat([tracker_df, pd.DataFrame([new_row])], ignore_index=True)
                            save_tracker(tracker_df)
                            st.success("✅ Parlay logged!")

            st.divider()

            # ── Parlay Builder ────────────────────────────────────────────────
            with st.expander("🏗️ Parlay Builder  —  pick your own legs", expanded=False):
                bettable = [s for s in signals if s["ml"] is not None and s["edge"] >= 0]
                leg_labels = [f"[{s['market']}] {s['side']}  {'+' if float(s['ml'])>0 else ''}{s['ml']} @ {s['book']}  ({int(s['model_p']*100)}%)" for s in bettable]
                selected_labels = st.multiselect("Select 2–6 legs", leg_labels, max_selections=6)
                selected = [bettable[leg_labels.index(lb)] for lb in selected_labels if lb in leg_labels]

                if len(selected) >= 2:
                    def _to_dec(american):
                        o = float(american)
                        return (o/100)+1 if o>0 else (100/abs(o))+1
                    def _dec_to_amr(dec):
                        return f"+{int((dec-1)*100)}" if dec>=2 else f"{int(-100/(dec-1))}"

                    parlay_dec  = 1.0
                    parlay_prob = 1.0
                    for leg in selected:
                        parlay_dec  *= _to_dec(leg["ml"])
                        parlay_prob *= leg["model_p"]

                    parlay_amr    = _dec_to_amr(parlay_dec)
                    parlay_edge   = parlay_prob - 1/parlay_dec
                    parlay_payout = round((parlay_dec-1)*100, 2)

                    pc1,pc2,pc3,pc4 = st.columns(4)
                    pc1.metric("Legs",         len(selected))
                    pc2.metric("Parlay Odds",  parlay_amr)
                    pc3.metric("Model Hit %",  f"{parlay_prob*100:.1f}%")
                    pc4.metric("Edge",         f"{parlay_edge*100:+.1f}%",
                               delta_color="normal" if parlay_edge>0 else "inverse")
                    st.caption(f"Win on $100 bet: **${parlay_payout:,.0f}**")

                    leg_summary = " + ".join([f"{s['side']} ({'+' if float(s['ml'])>0 else ''}{s['ml']})" for s in selected])
                    if st.button("📋 Log Parlay to Tracker", key="log_custom_parlay", use_container_width=True):
                        today_str = date.today().strftime("%m/%d/%Y")
                        games_str = " + ".join(list(dict.fromkeys(s["game"] for s in selected)))
                        new_row = {
                            "Date": today_str, "Game": games_str,
                            "Bet_Side": f"Parlay: {leg_summary}",
                            "Market": "Parlay", "Book": " / ".join(s["book"] for s in selected),
                            "Bet_ML": parlay_amr,
                            "Model_Prob": round(parlay_prob*100,1),
                            "Market_Implied": round(1/parlay_dec*100,1),
                            "Edge_Pct": round(parlay_edge*100,1),
                            "Park_Factor":"","Ump_K_Boost":"","Away_LU_Score":"","Home_LU_Score":"",
                            "Wager": 100, "F5_Score":"","Result":"PENDING",
                            "Profit_Loss":"","Closing_ML":"","CLV":"",
                            "Notes": f"Custom {len(selected)}-leg parlay",
                        }
                        tracker_df = pd.concat([tracker_df, pd.DataFrame([new_row])], ignore_index=True)
                        save_tracker(tracker_df)
                        st.success("✅ Parlay logged!")
                elif len(selected) == 1:
                    st.info("Add at least one more leg.")
                else:
                    st.caption("Select 2–6 legs above to build a parlay.")

            st.divider()

            market_filter = st.multiselect("Filter by Market",
                ["F5 ML","F5 Spread","F5 Total","F5 Team Total","NRFI/YRFI","1st Inn U1.5"],
                default=["F5 ML","F5 Spread","F5 Total","F5 Team Total","NRFI/YRFI","1st Inn U1.5"])
            st.divider()

            display_signals = [s for s in signals
                                if s["edge"] >= min_edge
                                and s.get("market","F5 ML") in market_filter
                                and s["model_p"] >= min_conf]

            for rank, s in enumerate(display_signals):
                if s["model_p"] >= 0.60:
                    css, dot = "bet-strong",  "dot-high"
                    badge_label = "HIGH CONFIDENCE"
                    bar_color = "linear-gradient(90deg,#00e676,#00bcd4)"
                elif s["model_p"] >= 0.55:
                    css, dot = "bet-moderate", "dot-solid"
                    badge_label = "SOLID PICK"
                    bar_color = "linear-gradient(90deg,#ffd600,#ff9800)"
                else:
                    css, dot = "no-edge", "dot-lean"
                    badge_label = "LEAN"
                    bar_color = "linear-gradient(90deg,#607d8b,#455a64)"

                # Market badge class
                mkt = s.get("market","F5 ML")
                mkt_cls = {"F5 ML":"mkt-ml","F5 Spread":"mkt-spread","F5 Total":"mkt-total","F5 Team Total":"mkt-team"}.get(mkt,"mkt-ml")

                edge_color = "#00e676" if s['edge'] >= 0.05 else "#ffd600" if s['edge'] >= 0.01 else "#78909c"
                edge_label = (f"+{s['edge']*100:.1f}% edge" if s['edge'] >= 0.01 else "Model only")

                cal_p   = calibrate_prob(s["model_p"]*100, cal_map)
                cal_txt = f'<span style="color:#78909c;font-size:0.78rem"> cal {cal_p:.1f}%</span>' if cal_map and abs(cal_p - s["model_p"]*100) >= 1 else ""
                lu_txt  = f" &nbsp;|&nbsp; LU: <b>{s['lu_score']:.0f}</b>/100" if s['lu_score'] else ""

                # Matchup badge
                _plat = s.get("platoon_adv"); _hand = s.get("opp_hand","R"); _mscr = s.get("matchup_score")
                if _mscr and _plat is not None:
                    _pc = "#00e676" if _plat >= 3 else "#ff7043" if _plat <= -3 else "#b0bec5"
                    matchup_txt = (f'<span class="metric-pill" style="border-color:{_pc};color:{_pc}">'
                                   f'vs {_hand}HP: <b>{_plat:+.0f}</b> matchup</span>')
                else:
                    matchup_txt = ""

                # Form badge (recent performance + rest)
                _form = s.get("form_score", 0) or 0
                _rest = s.get("days_rest")
                if abs(_form) >= 2 or (_rest is not None and _rest <= 3):
                    _fc = "#00e676" if _form >= 3 else "#ff7043" if _form <= -3 else "#ffd600"
                    _rest_str = f" {_rest}d rest" if _rest is not None else ""
                    form_txt = (f'<span class="metric-pill" style="border-color:{_fc};color:{_fc}">'
                                f'Form: <b>{_form:+.0f}</b>{_rest_str}</span>')
                else:
                    form_txt = ""

                # Weather badge
                _wx = s.get("weather") or {}
                if _wx and not _wx.get("is_dome") and _wx.get("wind_speed", 0) >= 8:
                    _wm = _wx.get("wind_multiplier", 1.0)
                    _wc = "#ff7043" if _wm >= 1.06 else "#64b5f6" if _wm <= 0.94 else "#b0bec5"
                    _wlbl = "OUT" if _wm > 1.02 else "IN" if _wm < 0.98 else "CROSS"
                    _wspd = int(_wx.get("wind_speed", 0))
                    _wdir = str(_wx.get("wind_dir", ""))
                    _wtemp = str(_wx.get("temp", "?"))
                    weather_txt = (f'<span class="metric-pill" style="border-color:{_wc};color:{_wc}">'
                                   f'Wind {_wspd}mph {_wdir} ({_wlbl}) {_wtemp}F</span>')
                elif _wx and not _wx.get("is_dome") and _wx.get("temp") is not None:
                    weather_txt = f'<span class="metric-pill">{_wx["temp"]}F</span>'
                else:
                    weather_txt = ""
                pf_txt  = f"Park {s['park_factor']:.2f}x"
                _uz = s.get("ump_zone", 1.0) or 1.0
                _zone_str = f" Z{_uz:.2f}" if abs(_uz - 1.0) >= 0.03 else ""
                ump_txt = f"Ump K{s['ump_k']:+.2f}{_zone_str}" if s['ump_k'] else ""

                # Bullpen fatigue badge (show when unusually high or low)
                _bp_pill = ""
                _s_away_bp = s.get("away_bp_ip"); _s_home_bp = s.get("home_bp_ip")
                _league_bp_avg = 10.5
                if _s_away_bp is not None or _s_home_bp is not None:
                    _bp_parts = []
                    for _bp_val, _bp_label in [(_s_away_bp,"Away"), (_s_home_bp,"Home")]:
                        if _bp_val is not None:
                            _bp_excess = _bp_val - _league_bp_avg
                            if abs(_bp_excess) >= 3.0:
                                _bp_lbl = "🔥" if _bp_excess >= 3 else "❄️"
                                _bp_parts.append(f"{_bp_lbl}{_bp_label} BP {_bp_val:.1f}IP")
                    if _bp_parts:
                        _bp_pill = f'<span class="metric-pill" style="border-color:#90a4ae;color:#90a4ae">' + " · ".join(_bp_parts) + '</span>'

                # NRFI/YRFI — build B/P matchup pill if OPS data is available
                _nrfi_pill = ""
                if s.get("market") in ("NRFI/YRFI", "1st Inn U1.5"):
                    _aw_nr = s.get("away_nrfi", {}) or {}
                    _hw_nr = s.get("home_nrfi", {}) or {}
                    _parts = []
                    for _label, _nr in [("Away", _aw_nr), ("Home", _hw_nr)]:
                        if _nr.get("season_ops"):
                            if _nr.get("vs_sp_ops") and (_nr.get("vs_sp_pa") or 0) >= 8:
                                _parts.append(f"{_label} top-3: {_nr['season_ops']:.3f} / vs SP {_nr['vs_sp_ops']:.3f} ({_nr['vs_sp_pa']} PA)")
                            else:
                                _parts.append(f"{_label} top-3: {_nr['season_ops']:.3f} OPS")
                    if _parts:
                        _nrfi_pill = f'<span class="metric-pill" style="border-color:#7986cb;color:#7986cb">' + " · ".join(_parts) + '</span>'
                ml_str  = (f"{'+' if s['ml']>0 else ''}{s['ml']}" if s['ml'] else "-")
                _mkt_line_val = s.get("mkt_line")
                _mod_line_val = s.get("model_line")
                if _mod_line_val not in ("", "-", None, "—"):
                    try:
                        _mkt_fmt = f"{float(_mkt_line_val):.2f}" if _mkt_line_val not in ("", "-", None, "—") else str(_mkt_line_val)
                        _mod_fmt = f"{float(_mod_line_val):.2f}"
                        line_txt = f'<span class="metric-pill">Line: <b>{_mod_fmt}</b> vs <b>{_mkt_fmt}</b></span>'
                    except (TypeError, ValueError):
                        line_txt = ""
                else:
                    line_txt = ""
                top_ribbon   = '<span class="top-pick-ribbon">⭐ TOP PICK</span>' if rank == 0 else ""
                sharp_ref_txt = ""
                # Line movement pill
                _mv_delta, _mv_dir = get_line_movement(
                    s.get("game_key", s["game"]),
                    next((k for k,v in BOOK_LABELS.items() if v==s["book"]), ""),
                    "away_ml" if "away" in s.get("side","").lower() else "home_ml",
                    s["ml"] or 0, odds_snapshot)
                if _mv_delta and _mv_delta >= 5:
                    _mv_color = "#00e676" if _mv_dir=="better" else "#ff7043"
                    _mv_arrow = "▲" if _mv_dir=="better" else "▼"
                    move_txt = f'<span class="metric-pill" style="border-color:{_mv_color};color:{_mv_color}">{_mv_arrow}{_mv_delta} moved</span>'
                else:
                    move_txt = ""
                # SP scratch warning pill
                scratch_txt = '<span class="metric-pill" style="border-color:#ff5722;color:#ff5722;font-weight:700">⚠️ SP CHANGE</span>' if s.get("sp_scratch") else ""
                conf_pct   = int(s["model_p"] * 100)

                # DraftKings-style logo block: always both teams side by side,
                # active team full opacity, opposing team dimmed.
                # For game totals, both at full opacity (no active team).
                a_abv  = s.get("away_abv", s["abv"])
                h_abv  = s.get("home_abv", s["abv"])
                mkt    = s.get("market","F5 ML")
                is_total = mkt == "F5 Total"
                # Determine which side is active to set opacity
                away_op = "1.0" if (is_total or s["abv"] == a_abv) else "0.35"
                home_op = "1.0" if (is_total or s["abv"] == h_abv) else "0.35"
                logo_html = f"""
                  <div style="display:flex;align-items:center;gap:6px;flex-shrink:0">
                    <div style="text-align:center;opacity:{away_op};transition:opacity 0.2s">
                      <img src="{logo_url(a_abv)}" width="38"
                           style="border-radius:6px;display:block;margin:0 auto"/>
                      <div style="font-size:0.6rem;font-weight:800;color:#8ab4d4;
                                  margin-top:3px;letter-spacing:0.04em">{a_abv.upper()}</div>
                    </div>
                    <div style="color:#3a4a5e;font-size:0.75rem;font-weight:700;
                                margin-bottom:12px;padding:0 2px">@</div>
                    <div style="text-align:center;opacity:{home_op};transition:opacity 0.2s">
                      <img src="{logo_url(h_abv)}" width="38"
                           style="border-radius:6px;display:block;margin:0 auto"/>
                      <div style="font-size:0.6rem;font-weight:800;color:#8ab4d4;
                                  margin-top:3px;letter-spacing:0.04em">{h_abv.upper()}</div>
                    </div>
                  </div>"""

                # Pre-build pills as one string so empty vars don't leave blank
                # lines that would terminate Streamlit/CommonMark's HTML block mode.
                pills_html = "".join(filter(None, [
                    f'<span class="{mkt_cls}">{mkt}</span>',
                    scratch_txt,
                    f'<span class="metric-pill" style="border-color:{edge_color};color:{edge_color}"><b>{edge_label}</b></span>',
                    f'<span class="metric-pill">+{ml_str} @ {s["book"]}</span>',
                    move_txt,
                    f'<span class="metric-pill">Mkt: {s["mkt_p"]*100:.1f}%</span>',
                    f'<span class="metric-pill">SP: {s["sp_score"]:.0f}{lu_txt}</span>',
                    _nrfi_pill,
                    matchup_txt,
                    form_txt,
                    weather_txt,
                    _bp_pill,
                    line_txt,
                    sharp_ref_txt,
                    f'<span class="park-badge">{pf_txt}</span>',
                    f'<span class="ump-badge">{ump_txt}</span>' if ump_txt else "",
                ]))

                st.markdown(f"""<div class="{css}">
<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px"><div style="display:flex;align-items:center;gap:12px">{logo_html}<div><div style="font-size:1.05rem;font-weight:700;line-height:1.2"><span class="{dot}"></span>{badge_label}{top_ribbon}</div><div style="font-size:0.9rem;font-weight:600;margin-top:2px">{s['side']}</div><div style="font-size:0.78rem;color:#7a9cbf;margin-top:1px">{s['game']} | {s['time']}</div></div></div><div style="text-align:right"><div style="font-size:2rem;font-weight:800;line-height:1">{conf_pct}%</div><div style="font-size:0.7rem;color:#7a9cbf;text-transform:uppercase;letter-spacing:0.05em">Model Prob{cal_txt}</div></div></div>
<div class="conf-bar-wrap"><div class="conf-bar-fill" style="width:{conf_pct}%;background:{bar_color}"></div></div>
<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;margin-bottom:10px">{pills_html}</div>
<div style="display:flex;justify-content:space-between;align-items:center;border-top:1px solid rgba(255,255,255,0.06);padding-top:10px;margin-top:4px"><span style="color:#b0bec5;font-size:0.82rem">Suggested bet</span><span style="font-size:1.1rem;font-weight:700;color:#00e676">${s['kelly']:,.0f}</span></div>
</div>""", unsafe_allow_html=True)

                # One-click log button (below card, outside HTML)
                if s["ml"]:
                    today_str = date.today().strftime("%m/%d/%Y")
                    _mask = (tracker_df["Date"] == today_str) & (tracker_df["Bet_Side"] == s["side"])
                    if "Market" in tracker_df.columns:
                        _mask = _mask & (tracker_df["Market"] == s.get("market", "F5 ML"))
                    already_logged = not tracker_df[_mask].empty

                    btn_cols = st.columns([3, 1])
                    with btn_cols[1]:
                        if already_logged:
                            st.success("✓ Logged")
                        elif st.button(f"📋 Log ${bankroll:.0f}", key=f"log_{rank}_{s['game']}_{s['side']}", use_container_width=True):
                            new_row = {
                                "Date": today_str,
                                "Game": s["game"],
                                "Bet_Side": s["side"],
                                "Market": s.get("market", "F5 ML"),
                                "Book": s["book"],
                                "Bet_ML": s["ml"],
                                "Model_Prob": round(s["model_p"] * 100, 1),
                                "Market_Implied": round(s["mkt_p"] * 100, 1),
                                "Edge_Pct": round(s["edge"] * 100, 1),
                                "Park_Factor": s.get("park_factor", ""),
                                "Ump_K_Boost": s.get("ump_k", ""),
                                "Away_LU_Score": s.get("lu_score", ""),
                                "Home_LU_Score": "",
                                "Wager": bankroll,
                                "F5_Score": "", "Result": "PENDING",
                                "Profit_Loss": "", "Closing_ML": "", "CLV": "",
                                "Notes": "",
                            }
                            tracker_df = pd.concat([tracker_df, pd.DataFrame([new_row])], ignore_index=True)
                            save_tracker(tracker_df)
                            st.rerun()

            # Log bet (manual form fallback)
            st.divider(); st.subheader("➕ Log a Bet (Manual)")
            with st.form("log_bet"):
                loggable = [s for s in display_signals if s["ml"]]
                sel = st.selectbox("Select Signal",
                    [f"[{s['market']}] {s['side']} — {s['game']}" for s in loggable])
                wager = st.number_input("Wager ($)", min_value=1.0, value=float(bankroll))
                notes = st.text_input("Notes")
                if st.form_submit_button("Log Bet") and loggable:
                    idx = [f"[{s['market']}] {s['side']} — {s['game']}" for s in loggable].index(sel)
                    s = loggable[idx]
                    new = {"Date":date.today().strftime("%m/%d/%Y"),"Game":s["game"],
                           "Bet_Side":s["side"],"Market":s["market"],
                           "Book":s["book"],"Bet_ML":s["ml"],
                           "Model_Prob":round(s["model_p"]*100,1),
                           "Market_Implied":round(s["mkt_p"]*100,1),
                           "Edge_Pct":round(s["edge"]*100,1),
                           "Park_Factor":s["park_factor"],"Ump_K_Boost":s["ump_k"],
                           "Away_LU_Score":s.get("lu_score",""),
                           "Home_LU_Score":"","Wager":wager,
                           "F5_Score":"","Result":"PENDING","Profit_Loss":"",
                           "Closing_ML":"","CLV":"","Notes":notes}
                    tracker_df = pd.concat([tracker_df,pd.DataFrame([new])],ignore_index=True)
                    save_tracker(tracker_df)
                    st.success(f"✅ Logged: {sel}")

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: BEST BETS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "📚 Best Bets":
    st.title("📚 Best Bets by Book")
    st.caption("Top model signals organized by sportsbook — see your best play at each book at a glance.")

    # ── Line Shopping: per-game price grid ───────────────────────────────────
    if games:
        pregame = [g for g in games if datetime.strptime(g["commence_time"],"%Y-%m-%dT%H:%M:%SZ") > datetime.utcnow()]
        if pregame:
            with st.expander("🔍 Line Shopping — compare all books for a game", expanded=True):
                game_names = [f"{g['away_team']} @ {g['home_team']}" for g in pregame]
                sel_game_name = st.selectbox("Select game", game_names, label_visibility="collapsed")
                sel_game = pregame[game_names.index(sel_game_name)]
                _sa = sel_game["away_team"]; _sh = sel_game["home_team"]
                _odds = fetch_f5(sel_game["id"], _sa, _sh)

                # ML comparison
                ml_rows = []
                for bk, bn in BOOK_LABELS.items():
                    bdata = _odds["ml"].get(bk, {})
                    if bdata.get("away") or bdata.get("home"):
                        aw = bdata.get("away"); hw = bdata.get("home")
                        ml_rows.append({
                            "Book": bn,
                            f"{_sa[:12]} (Away)": f"{'+' if aw and aw>0 else ''}{aw}" if aw else "—",
                            f"{_sh[:12]} (Home)": f"{'+' if hw and hw>0 else ''}{hw}" if hw else "—",
                        })
                if ml_rows:
                    st.markdown("**F5 Moneyline**")
                    ml_df = pd.DataFrame(ml_rows)
                    # Highlight best odds in each column
                    st.dataframe(ml_df, hide_index=True, use_container_width=True)
                    # Best price callout
                    aw_vals = {bn: _odds["ml"].get(bk,{}).get("away") for bk,bn in BOOK_LABELS.items() if _odds["ml"].get(bk,{}).get("away")}
                    hw_vals = {bn: _odds["ml"].get(bk,{}).get("home") for bk,bn in BOOK_LABELS.items() if _odds["ml"].get(bk,{}).get("home")}
                    if aw_vals:
                        best_aw_bk = max(aw_vals, key=aw_vals.get)
                        best_hw_bk = max(hw_vals, key=hw_vals.get) if hw_vals else None
                        c1, c2 = st.columns(2)
                        av = aw_vals[best_aw_bk]
                        c1.success(f"Best Away: **{'+' if av>0 else ''}{av}** @ {best_aw_bk}")
                        if best_hw_bk:
                            hv = hw_vals[best_hw_bk]
                            c2.success(f"Best Home: **{'+' if hv>0 else ''}{hv}** @ {best_hw_bk}")

                # Spread comparison
                sp_rows = []
                for bk, bn in BOOK_LABELS.items():
                    bdata = _odds["spread"].get(bk, {})
                    aw = bdata.get("away",{}); hw = bdata.get("home",{})
                    if aw.get("price") or hw.get("price"):
                        sp_rows.append({
                            "Book": bn,
                            f"{_sa[:12]} Spread": f"{aw.get('line',''):+g} ({'+' if (aw.get('price') or 0)>0 else ''}{aw.get('price','')})" if aw.get("price") else "—",
                            f"{_sh[:12]} Spread": f"{hw.get('line',''):+g} ({'+' if (hw.get('price') or 0)>0 else ''}{hw.get('price','')})" if hw.get("price") else "—",
                        })
                if sp_rows:
                    st.markdown("**F5 Spread**")
                    st.dataframe(pd.DataFrame(sp_rows), hide_index=True, use_container_width=True)

                # Total + NRFI comparison
                tot_rows = []
                for bk, bn in BOOK_LABELS.items():
                    td = _odds["total"].get(bk,{})
                    fi = _odds["fi_total"].get(bk,{})
                    if td.get("over_line") or fi.get("nrfi_price"):
                        ov_p = td.get("over_price"); un_p = td.get("under_price")
                        nrfi = fi.get("nrfi_price"); u15  = fi.get("u15_price")
                        tot_rows.append({
                            "Book": bn,
                            f"F5 O{td.get('over_line','?')}": f"{'+' if ov_p and ov_p>0 else ''}{ov_p}" if ov_p else "—",
                            f"F5 U{td.get('over_line','?')}": f"{'+' if un_p and un_p>0 else ''}{un_p}" if un_p else "—",
                            "NRFI": f"{'+' if nrfi and nrfi>0 else ''}{nrfi}" if nrfi else "—",
                            "1st U1.5": f"{'+' if u15 and u15>0 else ''}{u15}" if u15 else "—",
                        })
                if tot_rows:
                    st.markdown("**F5 Total + 1st Inning**")
                    st.dataframe(pd.DataFrame(tot_rows), hide_index=True, use_container_width=True)

    st.divider()

    # Re-use signals from Bet Signals tab by running the same pipeline
    # We need signals already computed; they are in the same script scope
    # if this page is loaded after Bet Signals runs, signals exist.
    # To be safe, load from cache the same way.

    _cache_data  = load_cache()
    _cache_by_away = {g["away_team"]: g for g in _cache_data}
    _cache_by_home = {g["home_team"]: g for g in _cache_data}

    _now_utc_bb = datetime.utcnow()
    _bb_signals = []

    for _game in games:
        try:
            _dt = datetime.strptime(_game["commence_time"], "%Y-%m-%dT%H:%M:%SZ")
            if _dt <= _now_utc_bb: continue
        except: pass

        _away = _game["away_team"]; _home = _game["home_team"]
        _odds = fetch_f5(_game["id"], _away, _home)
        try: _time_et = fmt_time_et(_dt)
        except: _time_et = ""

        _cd = _cache_by_away.get(_away, _cache_by_home.get(_home, {}))
        _pf = _cd.get("park_factor", 1.0)
        _ump_k = _cd.get("ump_k_boost", 0.0)

        # Collect all book-specific signals (one entry per book per side per market)
        for _mkt_key, _mkt_label in [("ml","F5 ML"), ("spread","F5 Spread"),
                                      ("total","F5 Total"), ("fi_total","NRFI/YRFI")]:
            _mkt_data = _odds.get(_mkt_key, {})
            for _bk in REC_BOOKS:
                if _bk not in _mkt_data: continue
                _bk_label = BOOK_LABELS.get(_bk, _bk)
                _bdata = _mkt_data[_bk]

                if _mkt_key == "ml":
                    for _side_key, _team in [("away", _away), ("home", _home)]:
                        _price = _bdata.get(_side_key)
                        if not _price: continue
                        _mkt_p = american_to_prob(_price) or 0.524
                        _bb_signals.append({
                            "book": _bk_label, "game": f"{_away} @ {_home}",
                            "time": _time_et,
                            "side": f"{_team} F5 ML",
                            "market": "F5 ML", "ml": _price, "mkt_p": _mkt_p,
                            "park_factor": _pf,
                        })
                elif _mkt_key == "spread":
                    for _side_key, _team in [("away", _away), ("home", _home)]:
                        _sd = _bdata.get(_side_key, {})
                        _price = _sd.get("price"); _line = _sd.get("line")
                        if not _price or _line is None: continue
                        _sign = "+" if _line > 0 else ""
                        _bb_signals.append({
                            "book": _bk_label, "game": f"{_away} @ {_home}",
                            "time": _time_et,
                            "side": f"{_team} {_sign}{_line}",
                            "market": "F5 Spread", "ml": _price,
                            "mkt_p": american_to_prob(_price) or 0.524,
                            "park_factor": _pf,
                        })
                elif _mkt_key == "total":
                    _ov_p = _bdata.get("over_price"); _un_p = _bdata.get("under_price")
                    _line = _bdata.get("over_line")
                    if _line and _ov_p:
                        _bb_signals.append({
                            "book": _bk_label, "game": f"{_away} @ {_home}",
                            "time": _time_et, "side": f"Over {_line} (F5)",
                            "market": "F5 Total", "ml": _ov_p,
                            "mkt_p": american_to_prob(_ov_p) or 0.524, "park_factor": _pf,
                        })
                    if _line and _un_p:
                        _bb_signals.append({
                            "book": _bk_label, "game": f"{_away} @ {_home}",
                            "time": _time_et, "side": f"Under {_line} (F5)",
                            "market": "F5 Total", "ml": _un_p,
                            "mkt_p": american_to_prob(_un_p) or 0.524, "park_factor": _pf,
                        })
                elif _mkt_key == "fi_total":
                    for _pk, _slabel, _smkt in [
                        ("nrfi_price","NRFI","NRFI/YRFI"),
                        ("yrfi_price","YRFI","NRFI/YRFI"),
                        ("u15_price","1st Inn U1.5","1st Inn U1.5"),
                    ]:
                        _price = _bdata.get(_pk)
                        if not _price: continue
                        _bb_signals.append({
                            "book": _bk_label, "game": f"{_away} @ {_home}",
                            "time": _time_et, "side": f"{_slabel}",
                            "market": _smkt, "ml": _price,
                            "mkt_p": american_to_prob(_price) or 0.524, "park_factor": _pf,
                        })

    if not _bb_signals:
        st.info("No odds data yet. Refresh odds or check back closer to game time.")
    else:
        # Group by book, sort each book's signals by best price (most value to bettor)
        _by_book = {}
        for _s in _bb_signals:
            _by_book.setdefault(_s["book"], []).append(_s)
        for _bk in _by_book:
            _by_book[_bk].sort(key=lambda x: x["ml"], reverse=True)

        # Book display order matches BOOK_LABELS order
        _ordered_books = [BOOK_LABELS[k] for k in BOOK_LABELS if BOOK_LABELS[k] in _by_book]

        _BOOK_COLORS = {
            "DraftKings":  "#00d47e",
            "FanDuel":     "#1493ff",
            "BetMGM":      "#f5a623",
            "Caesars":     "#0066cc",
            "TheScore":    "#e81a2a",
            "Fanatics":    "#cc0000",
            "Hard Rock":   "#c8932a",
        }

        # 2 columns of book cards
        _cols = st.columns(2)
        for _i, _bk_name in enumerate(_ordered_books):
            _sigs = _by_book[_bk_name][:8]  # top 8 per book
            _color = _BOOK_COLORS.get(_bk_name, "#4a6fa5")
            with _cols[_i % 2]:
                _rows_html = ""
                for _s in _sigs:
                    _ml_str = f"+{_s['ml']}" if float(_s['ml']) > 0 else str(int(_s['ml']))
                    _imp = int(_s['mkt_p'] * 100)
                    _rows_html += f"""
<div style="display:flex;justify-content:space-between;align-items:center;
            padding:7px 0;border-bottom:1px solid rgba(255,255,255,0.05)">
  <div>
    <div style="font-size:0.88rem;font-weight:600">{_s['side']}</div>
    <div style="font-size:0.74rem;color:#7a9cbf">{_s['game']} · {_s['time']}</div>
  </div>
  <div style="text-align:right;min-width:80px">
    <span style="font-size:0.95rem;font-weight:700;color:{_color}">{_ml_str}</span>
    <div style="font-size:0.70rem;color:#5a8ab4">{_imp}% implied</div>
  </div>
</div>"""

                st.markdown(f"""
<div style="background:linear-gradient(145deg,#0a1a2e,#0f2040);
            border:1px solid {_color}44;border-radius:12px;
            padding:14px 16px;margin-bottom:14px">
  <div style="font-size:1.05rem;font-weight:800;color:{_color};
              border-bottom:1px solid {_color}33;padding-bottom:8px;margin-bottom:4px">
    {_bk_name}
  </div>
  {_rows_html if _rows_html else '<div style="color:#5a8ab4;font-size:0.82rem;padding:8px 0">No lines available yet</div>'}
</div>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: NRFI
# ══════════════════════════════════════════════════════════════════════════════
elif page == "⚾ NRFI":
    st.markdown("""
    <div style="background:linear-gradient(135deg,#0c1e42 0%,#1a0f2e 100%);
                border-radius:16px;padding:24px 28px;margin-bottom:20px;
                border:1px solid rgba(123,97,255,0.25);
                box-shadow:0 8px 32px rgba(0,0,0,0.4)">
      <div style="font-size:1.6rem;font-weight:800;letter-spacing:-0.02em">
        ⚾ NRFI / YRFI Analysis
      </div>
      <div style="font-size:0.9rem;color:#9b8fcc;margin-top:4px">
        First-inning run model · Top-3 OPS matchup · Best price across books
      </div>
    </div>
    """, unsafe_allow_html=True)

    if not games:
        st.info("No games today.")
    else:
        _now_utc_nr = datetime.utcnow()
        _nr_rows = []

        for _g in games:
            try:
                _dt = datetime.strptime(_g["commence_time"], "%Y-%m-%dT%H:%M:%SZ")
                _time_et = fmt_time_et(_dt)
                _started = _dt <= _now_utc_nr
            except:
                _time_et = ""; _started = False

            _away = _g["away_team"]; _home = _g["home_team"]
            _odds = fetch_f5(_g["id"], _away, _home)
            _fi   = _odds.get("fi_total", {})
            _cd   = cache_by_away.get(_away, cache_by_home.get(_home, {}))

            _pf       = _cd.get("park_factor", 1.0)
            _ump_k    = _cd.get("ump_k_boost", 0.0)
            _ump_name = _cd.get("ump_name", "")
            _a_lu     = _cd.get("away_lineup_score")
            _h_lu     = _cd.get("home_lineup_score")
            _a_sp     = _cd.get("away_sp", {})
            _h_sp     = _cd.get("home_sp", {})
            _a_nrfi   = _cd.get("away_nrfi_top3") or {}
            _h_nrfi   = _cd.get("home_nrfi_top3") or {}

            # SP effective scores (form + split adj)
            _asp = (_a_sp.get("sp_score") or 50) + (_a_sp.get("form_score",0) or 0) + (_a_sp.get("home_away_adj",0) or 0)
            _hsp = (_h_sp.get("sp_score") or 50) + (_h_sp.get("form_score",0) or 0) + (_h_sp.get("home_away_adj",0) or 0)
            _asp = max(0, min(100, _asp)); _hsp = max(0, min(100, _hsp))

            _a_match = _cd.get("away_matchup_score"); _h_match = _cd.get("home_matchup_score")
            _elu = round(_a_match*0.55+(_a_lu or 50)*0.45,1) if _a_match else (_a_lu or 50)
            _hlu = round(_h_match*0.55+(_h_lu or 50)*0.45,1) if _h_match else (_h_lu or 50)

            # Model probs
            _mnrfi = calc_nrfi_prob(_asp, _hsp, _elu, _hlu, _pf, _ump_k, _a_nrfi, _h_nrfi)
            _mu15  = calc_fi_u15_prob(_asp, _hsp, _elu, _hlu, _pf, _ump_k, _a_nrfi, _h_nrfi)
            _myrfi = round(1 - _mnrfi, 4)

            # Best prices across rec books
            _best_nrfi = _best_yrfi = _best_u15 = None
            _mkt_nrfi_p = None
            for _bk in REC_BOOKS:
                _bfi = _fi.get(_bk, {})
                _np = _bfi.get("nrfi_price"); _yp = _bfi.get("yrfi_price"); _up = _bfi.get("u15_price")
                if _np and (_best_nrfi is None or _np > _best_nrfi): _best_nrfi = _np
                if _yp and (_best_yrfi is None or _yp > _best_yrfi): _best_yrfi = _yp
                if _up and (_best_u15 is None or _up > _best_u15):   _best_u15  = _up

            if _best_nrfi: _mkt_nrfi_p = american_to_prob(_best_nrfi)

            # Edge
            _nrfi_edge = round((_mnrfi - _mkt_nrfi_p) * 100, 1) if _mkt_nrfi_p else None

            _nr_rows.append({
                "game": f"{_away} @ {_home}",
                "away": _away, "home": _home,
                "time": _time_et,
                "started": _started,
                "a_sp_name": _a_sp.get("name","TBD"),
                "h_sp_name": _h_sp.get("name","TBD"),
                "a_sp_score": round(_asp,1),
                "h_sp_score": round(_hsp,1),
                "a_xfip": _a_sp.get("xfip"), "h_xfip": _h_sp.get("xfip"),
                "a_kbb":  _a_sp.get("k_bb_pct"), "h_kbb": _h_sp.get("k_bb_pct"),
                "a_ops": _a_nrfi.get("season_ops"), "h_ops": _h_nrfi.get("season_ops"),
                "a_vssp": _a_nrfi.get("vs_sp_ops"), "h_vssp": _h_nrfi.get("vs_sp_ops"),
                "a_vspa": _a_nrfi.get("vs_sp_pa",0), "h_vspa": _h_nrfi.get("vs_sp_pa",0),
                "model_nrfi": _mnrfi, "model_yrfi": _myrfi, "model_u15": _mu15,
                "mkt_nrfi_p": _mkt_nrfi_p,
                "nrfi_edge": _nrfi_edge,
                "best_nrfi": _best_nrfi, "best_yrfi": _best_yrfi, "best_u15": _best_u15,
                "pf": _pf, "ump_k": _ump_k, "ump_name": _ump_name,
            })

        if not _nr_rows:
            st.info("No game data available yet.")
        else:
            # Sort: pre-game first, then by model NRFI% desc (strongest NRFI plays first)
            _nr_rows.sort(key=lambda r: (r["started"], -r["model_nrfi"]))

            for _r in _nr_rows:
                _abv_a = get_abv(_r["away"]); _abv_h = get_abv(_r["home"])
                _status = "🔴 Live/Final" if _r["started"] else _r["time"]

                # Edge badge color
                _edge = _r["nrfi_edge"]
                if _edge is not None and _edge >= 4:
                    _edge_color = "#4caf50"; _edge_label = f"+{_edge:.1f}% edge"
                elif _edge is not None and _edge >= 2:
                    _edge_color = "#ffb300"; _edge_label = f"+{_edge:.1f}% edge"
                elif _edge is not None:
                    _edge_color = "#5a8ab4"; _edge_label = f"{_edge:.1f}%"
                else:
                    _edge_color = "#5a8ab4"; _edge_label = "—"

                with st.container():
                    st.markdown('<div class="game-card">', unsafe_allow_html=True)

                    # ── Header row ──
                    _hc1, _hc2, _hc3 = st.columns([3, 1, 3])
                    with _hc1:
                        st.image(logo_url(_abv_a), width=40)
                        _a_sc = _r["a_sp_score"]
                        _a_scratch = bool(_last_word(probable_pitchers.get(_r["away"],"")))
                        st.markdown(f"**{_r['away']}**  \n"
                                    f"<span style='font-size:0.8rem;color:#6a9cbf'>"
                                    f"{_last_word(_r['a_sp_name'],'TBD')} · SP {_a_sc:.0f}</span>",
                                    unsafe_allow_html=True)
                    with _hc2:
                        st.markdown(f"<div style='text-align:center;padding-top:8px'>"
                                    f"<span style='color:#6a9cbf;font-size:0.85rem'>{_status}</span><br>"
                                    f"<span style='font-size:1.1rem;font-weight:700'>vs</span></div>",
                                    unsafe_allow_html=True)
                    with _hc3:
                        st.image(logo_url(_abv_h), width=40)
                        _h_sc = _r["h_sp_score"]
                        st.markdown(f"**{_r['home']}**  \n"
                                    f"<span style='font-size:0.8rem;color:#6a9cbf'>"
                                    f"{_last_word(_r['h_sp_name'],'TBD')} · SP {_h_sc:.0f}</span>",
                                    unsafe_allow_html=True)

                    st.divider()

                    # ── Model vs Market ──
                    _mc1, _mc2, _mc3, _mc4 = st.columns(4)
                    _mc1.metric("Model NRFI", f"{_r['model_nrfi']*100:.1f}%")
                    _mc2.metric("Model YRFI", f"{_r['model_yrfi']*100:.1f}%")
                    _mc3.metric("Mkt NRFI", f"{_r['mkt_nrfi_p']*100:.1f}%" if _r['mkt_nrfi_p'] else "—")
                    _mc4.metric("Edge", _edge_label,
                                delta_color="normal" if (_edge or 0) >= 2 else "off")

                    # ── Best prices row ──
                    _pc1, _pc2, _pc3, _pc4 = st.columns(4)
                    def _fmt_odds(o): return f"{'+' if o and o>0 else ''}{o}" if o else "—"
                    _pc1.markdown(f"**Best NRFI:** `{_fmt_odds(_r['best_nrfi'])}`")
                    _pc2.markdown(f"**Best YRFI:** `{_fmt_odds(_r['best_yrfi'])}`")
                    _pc3.markdown(f"**Best U1.5:** `{_fmt_odds(_r['best_u15'])}`")
                    _pc4.markdown(f"**Model U1.5:** `{_r['model_u15']*100:.1f}%`")

                    # ── SP detail + OPS matchup ──
                    with st.expander("SP Stats · Top-3 OPS Matchup · Context"):
                        _sc1, _sc2 = st.columns(2)
                        with _sc1:
                            st.markdown(f"**Away SP — {_r['a_sp_name']}**")
                            _a_xfip = f"{_r['a_xfip']:.2f}" if _r['a_xfip'] else "—"
                            _a_kbb  = f"{_r['a_kbb']:.1f}%" if _r['a_kbb'] else "—"
                            st.caption(f"xFIP {_a_xfip} · K-BB% {_a_kbb}")
                            if _r["a_ops"]:
                                _vssp_txt = (f" (vs SP: {_r['a_vssp']:.3f} OPS over {_r['a_vspa']} PA)"
                                             if _r["a_vssp"] and (_r["a_vspa"] or 0) >= 8 else "")
                                st.caption(f"Away top-3 OPS: {_r['a_ops']:.3f}{_vssp_txt}")
                            else:
                                st.caption("Away top-3 OPS: not available")
                        with _sc2:
                            st.markdown(f"**Home SP — {_r['h_sp_name']}**")
                            _h_xfip = f"{_r['h_xfip']:.2f}" if _r['h_xfip'] else "—"
                            _h_kbb  = f"{_r['h_kbb']:.1f}%" if _r['h_kbb'] else "—"
                            st.caption(f"xFIP {_h_xfip} · K-BB% {_h_kbb}")
                            if _r["h_ops"]:
                                _vssp_txt = (f" (vs SP: {_r['h_vssp']:.3f} OPS over {_r['h_vspa']} PA)"
                                             if _r["h_vssp"] and (_r["h_vspa"] or 0) >= 8 else "")
                                st.caption(f"Home top-3 OPS: {_r['h_ops']:.3f}{_vssp_txt}")
                            else:
                                st.caption("Home top-3 OPS: not available")

                        st.divider()
                        _ctx1, _ctx2, _ctx3 = st.columns(3)
                        _ctx1.metric("Park Factor", f"{_r['pf']:.2f}")
                        _ctx2.metric("Ump K Boost", f"{_r['ump_k']:+.3f}")
                        _ctx3.caption(f"Ump: {_r['ump_name'] or '—'}")

                    st.markdown('</div>', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: MORNING REPORT
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🌅 Morning Report":
    _today_str  = date.today().strftime("%A, %B %d, %Y")
    _n_games    = len(games)
    _sync_ts    = ""
    if os.path.exists("sync_status.json"):
        try:
            with open("sync_status.json") as _sf: _ss = json.load(_sf)
            _sync_ts = _ss.get("last_sync","")[:16]
        except: pass

    st.markdown(f"""
    <div style="background:linear-gradient(135deg,#0c1e42 0%,#1a0c10 100%);
                border-radius:16px;padding:24px 28px;margin-bottom:20px;
                border:1px solid rgba(255,160,50,0.3);
                box-shadow:0 8px 32px rgba(0,0,0,0.4)">
      <div style="font-size:1.6rem;font-weight:800;letter-spacing:-0.02em">🌅 Morning Report</div>
      <div style="font-size:0.9rem;color:#c8a060;margin-top:4px">
        {_today_str} &nbsp;·&nbsp; {_n_games} games on slate
        {'&nbsp;·&nbsp; Synced ' + _sync_ts if _sync_ts else ''}
      </div>
    </div>
    """, unsafe_allow_html=True)

    # Pull signals from session state (populated by visiting Bet Signals tab)
    _mr_signals = st.session_state.get("signals_cache", [])
    if st.session_state.get("signals_date") != str(date.today()):
        _mr_signals = []

    if not _mr_signals:
        st.info("Visit **🎯 Bet Signals** first to generate today's signals, then return here for the report.")
    else:
        # ── Top 5 plays ───────────────────────────────────────────────────────
        _top5 = [s for s in _mr_signals if s.get("ml") and s["edge"] >= 0][:5]
        _high  = [s for s in _mr_signals if s["model_p"] >= 0.60]
        _solid = [s for s in _mr_signals if 0.55 <= s["model_p"] < 0.60]

        _sm1, _sm2, _sm3, _sm4 = st.columns(4)
        _sm1.metric("Total Signals",     len(_mr_signals))
        _sm2.metric("🔥 High Conf",      len(_high))
        _sm3.metric("🟢 Solid",          len(_solid))
        _sm4.metric("Top Rec Wager",     f"${sum(s['kelly'] for s in _top5):,.0f}")

        st.subheader("⭐ Top Plays Today")
        for _i, _s in enumerate(_top5):
            _conf_pct = int(_s["model_p"] * 100)
            _ml_str   = f"{'+' if _s['ml']>0 else ''}{_s['ml']}"
            _edge_str = f"+{_s['edge']*100:.1f}%"
            _rank_lbl = ["#1 — TOP PICK ⭐","#2","#3","#4","#5"][_i]
            _css = "bet-strong" if _s["model_p"] >= 0.60 else "bet-moderate" if _s["model_p"] >= 0.55 else "no-edge"
            _mkt_badge = {
                "F5 ML":"mkt-ml","F5 Spread":"mkt-spread","F5 Total":"mkt-total",
                "F5 Team Total":"mkt-team","NRFI/YRFI":"mkt-ml","1st Inn U1.5":"mkt-ml"
            }.get(_s.get("market","F5 ML"), "mkt-ml")
            _a_abv = _s.get("away_abv", _s["abv"])
            _h_abv = _s.get("home_abv", _s["abv"])
            st.markdown(f"""<div class="{_css}" style="padding:14px 18px;margin:7px 0">
<div style="display:flex;justify-content:space-between;align-items:center">
  <div>
    <div style="font-size:0.7rem;color:#7a9cbf;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:3px">{_rank_lbl}</div>
    <div style="font-size:1rem;font-weight:700">{_s['side']}</div>
    <div style="font-size:0.78rem;color:#7a9cbf;margin-top:2px">{_s['game']} · {_s['time']}</div>
    <div style="margin-top:7px;display:flex;flex-wrap:wrap;gap:5px">
      <span class="{_mkt_badge}">{_s.get('market','F5 ML')}</span>
      <span class="metric-pill"><b>{_ml_str}</b> @ {_s['book']}</span>
      <span class="metric-pill" style="color:#00e676">{_edge_str} edge</span>
      <span class="metric-pill">SP: {_s['sp_score']:.0f}</span>
      <span class="park-badge">Park {_s['park_factor']:.2f}x</span>
    </div>
  </div>
  <div style="text-align:right;flex-shrink:0;margin-left:16px">
    <div style="font-size:1.8rem;font-weight:800;line-height:1">{_conf_pct}%</div>
    <div style="font-size:0.65rem;color:#7a9cbf;text-transform:uppercase">Model Prob</div>
    <div style="font-size:1rem;font-weight:700;color:#00e676;margin-top:4px">${_s['kelly']:,.0f}</div>
    <div style="font-size:0.65rem;color:#7a9cbf">Kelly Rec</div>
  </div>
</div>
</div>""", unsafe_allow_html=True)

        # ── Double of the Day ──────────────────────────────────────────────────
        _bettable_mr  = [s for s in _mr_signals if s["ml"] is not None and s["edge"] >= 0]
        _value_mr     = [s for s in _bettable_mr if 90 <= float(s["ml"]) <= 220]
        _pool_mr      = _value_mr if len(_value_mr) >= 2 else _bettable_mr
        _seen_g, _legs = set(), []
        for _s in _pool_mr:
            if _s["game"] not in _seen_g:
                _legs.append(_s); _seen_g.add(_s["game"])
            if len(_legs) == 2: break

        if len(_legs) == 2:
            def _d2a(o):
                o=float(o); return (o/100)+1 if o>0 else (100/abs(o))+1
            def _a2s(d):
                return f"+{int((d-1)*100)}" if d>=2 else f"{int(-100/(d-1))}"
            _pdec = _d2a(_legs[0]["ml"]) * _d2a(_legs[1]["ml"])
            _pamr = _a2s(_pdec); _pprob = int(_legs[0]["model_p"]*_legs[1]["model_p"]*100)
            _ppay = round((_pdec-1)*bankroll, 2)
            st.markdown(f"""
            <div style="background:linear-gradient(145deg,#0a1a2e,#0f2040);border-radius:14px;
                        padding:16px 20px;margin:16px 0 10px;
                        border:1px solid rgba(33,150,243,0.35)">
              <div style="font-size:0.75rem;color:#5a8ab4;text-transform:uppercase;letter-spacing:0.07em;margin-bottom:10px">⚡ Double of the Day</div>
              <div style="display:flex;gap:12px;flex-wrap:wrap">
                <div style="flex:1;min-width:180px;background:rgba(255,255,255,0.04);border-radius:8px;padding:10px 14px">
                  <div style="font-size:0.7rem;color:#5a8ab4;text-transform:uppercase">Leg 1</div>
                  <div style="font-weight:700;font-size:0.92rem;margin-top:2px">{_legs[0]['side']}</div>
                  <div style="color:#64b5f6;font-size:0.82rem">{'+' if _legs[0]['ml']>0 else ''}{_legs[0]['ml']} @ {_legs[0]['book']}</div>
                </div>
                <div style="flex:1;min-width:180px;background:rgba(255,255,255,0.04);border-radius:8px;padding:10px 14px">
                  <div style="font-size:0.7rem;color:#5a8ab4;text-transform:uppercase">Leg 2</div>
                  <div style="font-weight:700;font-size:0.92rem;margin-top:2px">{_legs[1]['side']}</div>
                  <div style="color:#64b5f6;font-size:0.82rem">{'+' if _legs[1]['ml']>0 else ''}{_legs[1]['ml']} @ {_legs[1]['book']}</div>
                </div>
                <div style="display:flex;gap:20px;align-items:center;flex-shrink:0;padding:10px 14px">
                  <div style="text-align:center"><div style="font-size:1.3rem;font-weight:800">{_pamr}</div><div style="font-size:0.65rem;color:#5a8ab4;text-transform:uppercase">Parlay</div></div>
                  <div style="text-align:center"><div style="font-size:1.3rem;font-weight:800">{_pprob}%</div><div style="font-size:0.65rem;color:#5a8ab4;text-transform:uppercase">Hit %</div></div>
                  <div style="text-align:center"><div style="font-size:1.3rem;font-weight:800;color:#00e676">+${_ppay:,.0f}</div><div style="font-size:0.65rem;color:#5a8ab4;text-transform:uppercase">Win/${bankroll:.0f}</div></div>
                </div>
              </div>
            </div>
            """, unsafe_allow_html=True)

        st.divider()

        # ── Quick-reference game table ─────────────────────────────────────────
        st.subheader("📋 Games Quick Reference")
        _mr_cache = {g["away_team"]: g for g in cache}
        _mr_cache.update({g["home_team"]: g for g in cache})
        _slate_rows = []
        for _g in games:
            _aw = _g["away_team"]; _hw = _g["home_team"]
            _cd  = _mr_cache.get(_aw, _mr_cache.get(_hw, {}))
            _pf  = _cd.get("park_factor", 1.0)
            _ump = _cd.get("ump_name","").split()[-1] if _cd.get("ump_name") else "—"
            _ukp = _cd.get("ump_k_boost",0.0)
            _a_sp = _last_word(_cd.get("away_sp",{}).get("name",""), "TBD")
            _h_sp = _last_word(_cd.get("home_sp",{}).get("name",""), "TBD")
            _a_sc = _cd.get("away_sp",{}).get("sp_score") or "—"
            _h_sc = _cd.get("home_sp",{}).get("sp_score") or "—"
            _wx   = _cd.get("weather") or {}
            _wx_str = (f"{_wx['temp']}°F {_wx['wind_speed']}mph {_wx['wind_dir']}"
                       if _wx and not _wx.get("is_dome") and _wx.get("wind_speed",0)>0
                       else "Dome" if _wx and _wx.get("is_dome") else "—")
            try:
                _dt  = datetime.strptime(_g["commence_time"],"%Y-%m-%dT%H:%M:%SZ")
                _tet = fmt_time_et(_dt)
            except: _tet = "—"
            _slate_rows.append({
                "Time(ET)": _tet,
                "Matchup":  f"{_aw[:12]} @ {_hw[:12]}",
                "Away SP":  f"{_a_sp} ({_a_sc})",
                "Home SP":  f"{_h_sp} ({_h_sc})",
                "Park":     f"{_pf:.2f}x",
                "Ump":      f"{_ump} K{_ukp:+.2f}",
                "Weather":  _wx_str,
            })
        if _slate_rows:
            st.dataframe(pd.DataFrame(_slate_rows), hide_index=True, use_container_width=True)

        # ── Shareable text report ──────────────────────────────────────────────
        st.divider()
        with st.expander("📋 Copy Shareable Text Report", expanded=False):
            _lines = [
                f"⚾ MLB F5 Morning Report — {_today_str}",
                f"{'='*50}",
                f"{_n_games} games on slate | {len(_mr_signals)} signals | {len(_high)} high-conf",
                "",
                "TOP PLAYS",
                "-"*40,
            ]
            for _i, _s in enumerate(_top5):
                _ml_s = f"{'+' if _s['ml']>0 else ''}{_s['ml']}"
                _lines.append(
                    f"{_i+1}. [{_s.get('market','F5 ML')}] {_s['side']}"
                    f"\n   {_ml_s} @ {_s['book']} | {int(_s['model_p']*100)}% model | "
                    f"+{_s['edge']*100:.1f}% edge | ${_s['kelly']:.0f} Kelly"
                    f"\n   {_s['game']} · {_s['time']}"
                )
            if len(_legs) == 2:
                _lines += [
                    "",
                    "DOUBLE OF THE DAY",
                    "-"*40,
                    f"Leg 1: {_legs[0]['side']}  {'+' if _legs[0]['ml']>0 else ''}{_legs[0]['ml']} @ {_legs[0]['book']}",
                    f"Leg 2: {_legs[1]['side']}  {'+' if _legs[1]['ml']>0 else ''}{_legs[1]['ml']} @ {_legs[1]['book']}",
                    f"Parlay: {_pamr}  |  {_pprob}% hit prob  |  +${_ppay:,.0f} on ${bankroll:.0f}",
                ]
            _lines += [
                "",
                "MATCHUPS",
                "-"*40,
            ]
            for _row in _slate_rows:
                _lines.append(
                    f"{_row['Time(ET)']:>10}  {_row['Matchup']:<26}  "
                    f"{_row['Away SP']:<16} vs {_row['Home SP']:<16}  "
                    f"Park:{_row['Park']}  {_row['Weather']}"
                )
            _lines += ["", f"Generated by MLB F5 Model · {_to_et(datetime.utcnow()).strftime('%I:%M %p')} ET"]
            st.text_area("", "\n".join(_lines), height=500, label_visibility="collapsed")

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: SP INPUT
# ══════════════════════════════════════════════════════════════════════════════
elif page == "✏️ SP Input":
    st.title("✏️ Starting Pitcher Input")
    st.info("Source: FanGraphs pitcher splits (1-5 innings view). Auto-populated by data_sync.py — manual override here.")
    with st.form("sp_form"):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Away Starter**")
            at = st.text_input("Away Team")
            ap = st.text_input("Away Pitcher")
            ah = st.selectbox("Hand",["R","L"],key="ah")
            ax = st.number_input("xFIP",0.0,9.0,4.00,0.01,key="ax")
            ak = st.number_input("K-BB% (decimal)",0.0,0.50,0.10,0.01,key="ak")
            ahh = st.number_input("Hard Hit% (decimal)",0.0,0.60,0.35,0.01,key="ahh")
        with c2:
            st.markdown("**Home Starter**")
            ht = st.text_input("Home Team")
            hp = st.text_input("Home Pitcher")
            hh = st.selectbox("Hand",["R","L"],key="hh")
            hx = st.number_input("xFIP",0.0,9.0,4.00,0.01,key="hx")
            hk = st.number_input("K-BB% (decimal)",0.0,0.50,0.10,0.01,key="hk")
            hhh = st.number_input("Hard Hit% (decimal)",0.0,0.60,0.35,0.01,key="hhh")
        if st.form_submit_button("💾 Save", use_container_width=True) and at and ht:
            rows = []
            for team,pitcher,hand,xfip,kbb,hhard in [
                (at,ap,ah,ax,ak,ahh),(ht,hp,hh,hx,hk,hhh)]:
                score = calc_sp_score(xfip,kbb,hhard)
                rows.append({"Team":team,"Pitcher":pitcher,"Hand":hand,
                              "xFIP":xfip,"K_BB_pct":kbb,"Hard_Hit_pct":hhard,"SP_Score":score})
            sp_df = sp_df[~sp_df["Team"].str.contains(f"{at}|{ht}",case=False,na=False)]
            sp_df = pd.concat([sp_df,pd.DataFrame(rows)],ignore_index=True)
            save_sp_data(sp_df); st.success("✅ Saved!"); st.rerun()
    if not sp_df.empty:
        st.divider(); st.subheader("Current SP Database")
        disp = sp_df.copy()
        disp["K_BB_pct"]    = (disp["K_BB_pct"]*100).round(1).astype(str)+"%"
        disp["Hard_Hit_pct"]= (disp["Hard_Hit_pct"]*100).round(1).astype(str)+"%"
        disp.columns = ["Team","Pitcher","Hand","xFIP","K-BB%","Hard Hit%","SP Score"]
        st.dataframe(disp, hide_index=True, use_container_width=True)
        if st.button("🗑️ Clear All"): save_sp_data(pd.DataFrame(columns=sp_df.columns)); st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: BET TRACKER
# ══════════════════════════════════════════════════════════════════════════════
elif page == "📈 Bet Tracker":
    st.markdown("""
    <div style="background:linear-gradient(135deg,#0c1e42 0%,#0a1a10 100%);
                border-radius:16px;padding:24px 28px;margin-bottom:20px;
                border:1px solid rgba(46,182,100,0.25);box-shadow:0 8px 32px rgba(0,0,0,0.4)">
      <div style="font-size:1.6rem;font-weight:800;letter-spacing:-0.02em">📈 Bet Tracker</div>
      <div style="font-size:0.9rem;color:#6abf88;margin-top:4px">Season P&amp;L · Win Rate · CLV · Streaks</div>
    </div>
    """, unsafe_allow_html=True)

    if tracker_df.empty:
        st.info("No bets logged yet. Log bets from the Bet Signals page.")
    else:
        tracker_df["Profit_Loss"] = tracker_df.apply(
            lambda r: calc_pnl(r) if r["Result"] in ["WIN","LOSS","PUSH"] else None, axis=1)
        settled = tracker_df[tracker_df["Result"].isin(["WIN","LOSS","PUSH"])]
        wins    = len(settled[settled["Result"]=="WIN"])
        losses  = len(settled[settled["Result"]=="LOSS"])
        pending = len(tracker_df[tracker_df["Result"]=="PENDING"])
        n       = wins + losses
        net     = tracker_df["Profit_Loss"].dropna().sum()
        wag     = tracker_df["Wager"].astype(float).sum()
        roi     = (net/wag*100) if wag>0 else 0

        # Streak
        streak_val, streak_type = 0, ""
        for res in reversed(tracker_df["Result"].tolist()):
            if res == "PENDING": continue
            if streak_val == 0: streak_type = res
            if res == streak_type: streak_val += 1
            else: break
        streak_txt = f"{'🔥' if streak_type=='WIN' else '❄️'} {streak_val} {streak_type}" if streak_val > 0 else "—"

        c1,c2,c3,c4,c5,c6,c7 = st.columns(7)
        c1.metric("Total Bets",  len(tracker_df))
        c2.metric("Record",      f"{wins}-{losses}")
        c3.metric("Win Rate",    f"{wins/n*100:.1f}%" if n>0 else "—")
        c4.metric("Net P&L",     f"${net:+,.2f}", delta_color="normal")
        c5.metric("ROI",         f"{roi:+.1f}%")
        c6.metric("Pending",     pending)
        c7.metric("Streak",      streak_txt)

        if n > 0:
            st.progress(wins/n, text=f"Win Rate: {wins/n*100:.1f}% ({wins}-{losses})")

        st.divider()

        # ── P&L Chart ────────────────────────────────────────────────────────
        chart_df = tracker_df[tracker_df["Profit_Loss"].notna()].copy()
        if not chart_df.empty:
            chart_df = chart_df.reset_index(drop=True)
            chart_df["Cumulative P&L"] = chart_df["Profit_Loss"].cumsum()
            chart_df["Bet #"] = chart_df.index + 1

            tab1, tab2, tab3 = st.tabs(["📈 Cumulative P&L", "📊 Win Rate by Market", "📉 CLV Tracker"])

            with tab1:
                st.line_chart(chart_df.set_index("Bet #")["Cumulative P&L"],
                              use_container_width=True, height=280)
                # Annotate best/worst points
                peak     = chart_df["Cumulative P&L"].max()
                trough   = chart_df["Cumulative P&L"].min()
                col1,col2 = st.columns(2)
                col1.metric("Peak P&L",   f"${peak:+,.2f}")
                col2.metric("Max Drawdown",f"${trough:+,.2f}")

            with tab2:
                mkt_grp = settled.groupby("Market").apply(
                    lambda g: pd.Series({
                        "Bets":   len(g),
                        "Wins":   sum(g["Result"]=="WIN"),
                        "Win%":   round(sum(g["Result"]=="WIN")/len(g)*100,1) if len(g)>0 else 0,
                        "P&L":    round(g["Profit_Loss"].dropna().sum(),2),
                    })).reset_index() if "Market" in settled.columns else pd.DataFrame()
                if not mkt_grp.empty:
                    st.dataframe(mkt_grp, hide_index=True, use_container_width=True)
                    st.bar_chart(mkt_grp.set_index("Market")["Win%"], height=220)
                else:
                    st.caption("Log bets with market type to see breakdown.")

            with tab3:
                clv_df = settled[settled["CLV"].notna() & (settled["CLV"] != "")].copy()
                if not clv_df.empty:
                    try:
                        clv_df["CLV"] = pd.to_numeric(clv_df["CLV"], errors="coerce")
                        clv_df = clv_df.dropna(subset=["CLV"])
                        avg_clv = clv_df["CLV"].mean()
                        pos_clv = len(clv_df[clv_df["CLV"] > 0])
                        st.metric("Avg CLV", f"{avg_clv:+.1f}", help="Positive = beat closing line")
                        st.metric("% Positive CLV", f"{pos_clv/len(clv_df)*100:.0f}%")
                        clv_df_reset = clv_df.reset_index(drop=True)
                        clv_df_reset["Bet #"] = clv_df_reset.index + 1
                        st.bar_chart(clv_df_reset.set_index("Bet #")["CLV"], height=220)
                    except: st.caption("CLV data unavailable.")
                else:
                    st.caption("Enter closing line when settling bets to track CLV.")

            st.divider()

        # ── Auto-settle ──────────────────────────────────────────────────────
        if not tracker_df[tracker_df["Result"]=="PENDING"].empty and live_scores:
            tracker_df, _auto_changed = auto_settle_f5(tracker_df, live_scores, clv_snapshot)
            if _auto_changed:
                save_tracker(tracker_df)
                _auto_n = len([r for _,r in tracker_df.iterrows() if r["Result"] in ("WIN","LOSS","PUSH") and r.get("F5_Score")])
                st.success(f"✅ Auto-settled {_auto_n} F5 result(s) from live scores.")
                st.rerun()

        # ── Quick settle ─────────────────────────────────────────────────────
        pending_df = tracker_df[tracker_df["Result"]=="PENDING"]
        if not pending_df.empty:
            st.subheader(f"✏️ Settle Results ({len(pending_df)} pending)")

            # One-click settle: table with inline buttons
            for idx, row in pending_df.iterrows():
                mkt = row.get("Market","F5 ML")
                ml_str = f"{'+' if float(str(row['Bet_ML']).replace('+',''))>0 else ''}{row['Bet_ML']}" if row.get("Bet_ML") else "—"
                with st.container():
                    st.markdown(f"""
                    <div style="background:rgba(15,28,58,0.6);border:1px solid rgba(46,117,182,0.2);
                                border-radius:10px;padding:12px 16px;margin-bottom:6px">
                      <span style="font-weight:700">{row['Bet_Side']}</span>
                      <span style="color:#7a9cbf;margin:0 8px">·</span>
                      <span style="color:#ffb74d">{mkt}</span>
                      <span style="color:#7a9cbf;margin:0 8px">·</span>
                      {ml_str}
                      <span style="color:#7a9cbf;margin:0 8px">·</span>
                      ${float(row['Wager']):.0f} wager
                      <span style="color:#7a9cbf;font-size:0.8rem;margin-left:8px">{row['Date']}</span>
                    </div>
                    """, unsafe_allow_html=True)
                    s1,s2,s3,s4,s5,s6 = st.columns([1,1,1,0.8,1.3,1.5])
                    if s1.button("✅ WIN",  key=f"w{idx}", use_container_width=True):
                        tracker_df.at[idx,"Result"]="WIN"; save_tracker(tracker_df); st.rerun()
                    if s2.button("❌ LOSS", key=f"l{idx}", use_container_width=True):
                        tracker_df.at[idx,"Result"]="LOSS"; save_tracker(tracker_df); st.rerun()
                    if s3.button("➖ PUSH", key=f"p{idx}", use_container_width=True):
                        tracker_df.at[idx,"Result"]="PUSH"; save_tracker(tracker_df); st.rerun()
                    if s4.button("🗑️", key=f"del_p{idx}", use_container_width=True, help="Delete this bet"):
                        tracker_df = tracker_df.drop(index=idx).reset_index(drop=True)
                        save_tracker(tracker_df); st.rerun()
                    new_wager = s5.number_input("Wager", value=float(row.get("Wager",0) or 0),
                                                min_value=1.0, step=5.0, key=f"wg{idx}",
                                                label_visibility="collapsed")
                    if new_wager != float(row.get("Wager",0) or 0):
                        tracker_df.at[idx,"Wager"] = new_wager; save_tracker(tracker_df)
                    closing = s6.number_input("Closing ML", value=0, key=f"cl{idx}", label_visibility="collapsed")
                    if closing:
                        try:
                            tracker_df.at[idx,"Closing_ML"] = closing
                            tracker_df.at[idx,"CLV"] = float(str(row["Bet_ML"]).replace("+","")) - closing
                            save_tracker(tracker_df)
                        except: pass

            st.divider()

        # ── Full log ──────────────────────────────────────────────────────────
        st.subheader("📋 Full Log")
        display_cols = ["Date","Game","Bet_Side","Market","Book","Bet_ML",
                        "Model_Prob","Edge_Pct","Wager","Result","Profit_Loss","CLV","Notes"]
        show_cols = [c for c in display_cols if c in tracker_df.columns]
        st.dataframe(tracker_df[show_cols].sort_values("Date",ascending=False)
                     if not tracker_df.empty else tracker_df,
                     hide_index=True, use_container_width=True)

        # ── Edit / Delete ──────────────────────────────────────────────────────
        if not tracker_df.empty:
            with st.expander(f"✏️ Edit or Delete Bets ({len(tracker_df)} total)", expanded=False):
                for idx, row in tracker_df.sort_values("Date", ascending=False).iterrows():
                    _res_color = {"WIN":"#00e676","LOSS":"#ff5252","PENDING":"#ffb74d","PUSH":"#90a4ae"}.get(str(row.get("Result","")),"#90a4ae")
                    _label = f"{row.get('Date','')}  ·  {str(row.get('Bet_Side',''))[:40]}  ·  {str(row.get('Market',''))}"
                    with st.container():
                        st.markdown(f"""
                        <div style="display:flex;justify-content:space-between;align-items:center;
                                    background:rgba(15,28,58,0.5);border-radius:8px;
                                    padding:8px 14px;margin-bottom:4px">
                          <span style="font-size:0.84rem">{_label}</span>
                          <span style="font-size:0.78rem;color:{_res_color};font-weight:700">{row.get('Result','')}</span>
                        </div>
                        """, unsafe_allow_html=True)
                        _ec1, _ec2, _ec3 = st.columns([2, 1, 0.6])
                        _cur_wager = float(row.get("Wager", 0) or 0)
                        _new_wager = _ec1.number_input(
                            f"Wager for row {idx}", value=_cur_wager,
                            min_value=1.0, step=5.0,
                            key=f"edit_wg_{idx}", label_visibility="collapsed")
                        if _new_wager != _cur_wager:
                            tracker_df.at[idx, "Wager"] = _new_wager
                            save_tracker(tracker_df)
                            st.success("Updated.")
                        if _ec2.button("💾 Save Wager", key=f"save_wg_{idx}", use_container_width=True):
                            tracker_df.at[idx, "Wager"] = _new_wager
                            save_tracker(tracker_df); st.rerun()
                        if _ec3.button("🗑️ Delete", key=f"del_all_{idx}", use_container_width=True):
                            tracker_df = tracker_df.drop(index=idx).reset_index(drop=True)
                            save_tracker(tracker_df); st.rerun()

        st.divider()
        col1, col2 = st.columns(2)
        col1.download_button("📥 Download CSV",
            tracker_df.to_csv(index=False).encode(), "f5_bets.csv", "text/csv")
        with col2:
            up = st.file_uploader("📤 Import CSV", type="csv", label_visibility="collapsed")
            if up:
                imported = pd.read_csv(up)
                save_tracker(imported); st.success("✅ Imported!"); st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: PARK FACTORS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🏟️ Park Factors":
    st.title("🏟️ F5 Park Factors")
    st.info("F5-specific park factors representing run environment through 5 innings. "
            "1.00 = neutral. Updated annually based on multi-year F5 run data.")

    PARK_TEAMS_APP = {
        "Coors Field":"Colorado Rockies","Great American Ball Park":"Cincinnati Reds",
        "Globe Life Field":"Texas Rangers","Fenway Park":"Boston Red Sox",
        "Wrigley Field":"Chicago Cubs","Kauffman Stadium":"Kansas City Royals",
        "Angel Stadium":"Los Angeles Angels","American Family Field":"Milwaukee Brewers",
        "Guaranteed Rate Field":"Chicago White Sox","Rogers Centre":"Toronto Blue Jays",
        "Truist Park":"Atlanta Braves","Chase Field":"Arizona Diamondbacks",
        "Camden Yards":"Baltimore Orioles","Yankee Stadium":"New York Yankees",
        "Citizens Bank Park":"Philadelphia Phillies","Nationals Park":"Washington Nationals",
        "T-Mobile Park":"Seattle Mariners","Target Field":"Minnesota Twins",
        "Dodger Stadium":"Los Angeles Dodgers","Minute Maid Park":"Houston Astros",
        "Busch Stadium":"St. Louis Cardinals","LoanDepot Park":"Miami Marlins",
        "Oracle Park":"San Francisco Giants","PNC Park":"Pittsburgh Pirates",
        "Tropicana Field":"Tampa Bay Rays","Petco Park":"San Diego Padres",
        "Progressive Field":"Cleveland Guardians","Oakland Coliseum":"Oakland Athletics",
        "Comerica Park":"Detroit Tigers","Citi Field":"New York Mets",
    }
    rows = []
    for park, pf in sorted(PARK_FACTORS.items(), key=lambda x: x[1], reverse=True):
        cls = ("🔴 Hitter Friendly" if pf>=1.05 else "🟡 Slight Hitter" if pf>=1.01
               else "⚪ Neutral" if pf>=0.99 else "🟡 Slight Pitcher" if pf>=0.96
               else "🔵 Pitcher Friendly")
        team = PARK_TEAMS_APP.get(park,"")
        abv  = get_abv(team) if team else "mlb"
        rows.append({"🏟️ Park":park,"Team":team,"F5 Factor":pf,"Classification":cls})

    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, use_container_width=True)
    st.caption("Factors updated annually. Coors Field (+28%) and Great American Ball Park (+12%) are the most significant outliers.")

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: MODEL PERFORMANCE
# ══════════════════════════════════════════════════════════════════════════════
elif page == "📊 Model Performance":
    st.title("📊 Model Performance & Learning")
    st.caption("Signals ≥60% model confidence are auto-tracked here. Settle results to grade the model.")

    if model_picks_df.empty:
        st.info("No model picks tracked yet. Signals ≥52% model prob are auto-logged on game days.")
    else:
        settled = model_picks_df[model_picks_df["Result"].isin(["WIN","LOSS"])].copy()
        pending_picks = model_picks_df[model_picks_df["Result"]=="PENDING"]
        wins   = len(settled[settled["Result"]=="WIN"])
        losses = len(settled[settled["Result"]=="LOSS"])
        n      = wins + losses

        c1,c2,c3,c4,c5,c6 = st.columns(6)
        c1.metric("Total Tracked", len(model_picks_df))
        c2.metric("Settled",       n)
        c3.metric("Pending",       len(pending_picks))
        c4.metric("Record",        f"{wins}-{losses}")
        c5.metric("Win Rate",      f"{wins/n*100:.1f}%" if n>0 else "—")

        # Market breakdown
        by_mkt = model_picks_df[model_picks_df["Result"].isin(["WIN","LOSS"])].groupby("Market").apply(
            lambda g: pd.Series({"W":sum(g["Result"]=="WIN"),"L":sum(g["Result"]=="LOSS")})).reset_index()
        if not by_mkt.empty:
            by_mkt["Win%"] = (by_mkt["W"]/(by_mkt["W"]+by_mkt["L"])*100).round(1).astype(str)+"%"
            c6.metric("Markets", len(by_mkt))

        if n > 0:
            st.divider()
            # ── Calibration ───────────────────────────────────────────────────
            st.subheader("🎯 Probability Calibration")
            st.caption("Does the model's confidence match reality? When it says 60%, does it hit 60% of the time?")
            settled["Prob_Bucket"] = (settled["Model_Prob"] // 5 * 5).astype(int)
            cal_rows = []
            for bucket in sorted(settled["Prob_Bucket"].unique()):
                grp = settled[settled["Prob_Bucket"]==bucket]
                w   = len(grp[grp["Result"]=="WIN"])
                tot = len(grp)
                actual = w/tot*100
                expected = bucket + 2.5
                bias = actual - expected
                bias_icon = "🟢" if abs(bias) <= 3 else "🟡" if abs(bias) <= 6 else "🔴"
                cal_rows.append({
                    "Prob Range":  f"{bucket}-{bucket+5}%",
                    "# Picks":     tot,
                    "Expected Win%": f"{expected:.1f}%",
                    "Actual Win%":   f"{actual:.1f}%",
                    "Bias":          f"{bias_icon} {bias:+.1f}%",
                    "Record":        f"{w}-{tot-w}",
                })
            st.dataframe(pd.DataFrame(cal_rows), hide_index=True, use_container_width=True)

            overall_wr = wins/n*100
            if overall_wr >= 55:
                st.success(f"✅ Model is performing well — {overall_wr:.1f}% overall win rate on tracked signals.")
            elif overall_wr >= 50:
                st.info(f"📊 Model is slightly above break-even ({overall_wr:.1f}%). Keep building the sample.")
            else:
                st.warning(f"⚠️ Model is below break-even ({overall_wr:.1f}%). Consider raising the min edge threshold.")

            # Market breakdown table
            if not by_mkt.empty:
                st.divider()
                st.subheader("📈 Performance by Market")
                st.dataframe(by_mkt.rename(columns={"Market":"Market","W":"Wins","L":"Losses"}),
                             hide_index=True, use_container_width=True)

        if n >= 20:
            st.divider()
            st.subheader("🔬 Factor Correlation with Wins")
            st.caption("Which model inputs actually predict outcomes? Higher correlation = more predictive.")
            try:
                import numpy as np
                settled_num = settled.copy()
                settled_num["Win_Binary"] = (settled_num["Result"]=="WIN").astype(int)
                factor_rows = []
                for col, label in [("SP_Score","SP Score"),("LU_Score","Lineup Quality"),
                                    ("Park_Factor","Park Factor"),("Ump_K","Ump K Boost"),
                                    ("Edge_Pct","Edge %"),("Model_Prob","Model Prob")]:
                    try:
                        vals = pd.to_numeric(settled_num[col], errors="coerce").dropna()
                        if len(vals) < 10: continue
                        corr = float(np.corrcoef(vals.values, settled_num.loc[vals.index,"Win_Binary"].values)[0,1])
                        signal = "↑ Helpful" if corr > 0.05 else "↓ Hurting" if corr < -0.05 else "≈ Neutral"
                        factor_rows.append({"Factor":label,"Correlation":round(corr,3),"Signal":signal})
                    except: continue
                if factor_rows:
                    fdf = pd.DataFrame(factor_rows).sort_values("Correlation",ascending=False)
                    st.dataframe(fdf, hide_index=True, use_container_width=True)

                    if n >= 30:
                        st.subheader("💡 Weight Adjustment Suggestions")
                        sp_c  = next((f["Correlation"] for f in factor_rows if f["Factor"]=="SP Score"),    None)
                        lu_c  = next((f["Correlation"] for f in factor_rows if f["Factor"]=="Lineup Quality"), None)
                        pk_c  = next((f["Correlation"] for f in factor_rows if f["Factor"]=="Park Factor"), None)
                        ump_c = next((f["Correlation"] for f in factor_rows if f["Factor"]=="Ump K Boost"), None)
                        if sp_c is not None and lu_c is not None:
                            if sp_c > lu_c + 0.10:
                                st.info("📊 SP Score is more predictive than Lineup Quality. Consider increasing the SP weight slider.")
                            elif lu_c > sp_c + 0.10:
                                st.info("📊 Lineup Quality is more predictive than SP Score. Consider increasing the Lineup weight slider.")
                            else:
                                st.success("✅ SP and Lineup weights appear balanced based on history.")
                        if pk_c is not None and abs(pk_c) < 0.03:
                            st.info("📊 Park Factor shows low correlation with outcomes. Consider reducing its weight.")
                        if ump_c is not None and abs(ump_c) < 0.03:
                            st.info("📊 Ump tendency shows low correlation. Consider reducing its weight.")
            except Exception as e:
                st.caption(f"Correlation analysis unavailable: {e}")

        # ── Settle pending model picks ─────────────────────────────────────────
        if not pending_picks.empty:
            st.divider()
            st.subheader("✏️ Settle Model Picks")
            for idx, row in pending_picks.iterrows():
                mkt_disp = row.get("Market","F5 ML")
                with st.expander(f"{row['Date']} | [{mkt_disp}] {row['Side']} | Model: {row['Model_Prob']}% | Edge: {row['Edge_Pct']}%"):
                    r1, r2 = st.columns(2)
                    result = r1.selectbox("Result",["PENDING","WIN","LOSS","PUSH"],key=f"mp_r{idx}")
                    score  = r2.text_input("F5 Score (e.g. 3-2)",key=f"mp_s{idx}")
                    if st.button("Update",key=f"mp_u{idx}"):
                        model_picks_df.at[idx,"Result"]   = result
                        model_picks_df.at[idx,"F5_Score"] = score
                        save_model_picks(model_picks_df)
                        st.success("Updated!"); st.rerun()

        # ── Full history ───────────────────────────────────────────────────────
        st.divider()
        st.subheader("📋 Full Model Pick History (≥60%)")
        st.caption("Model_Prob = model's raw confidence · Market_Prob = market implied · Edge = difference · Use these to grade.")
        if not model_picks_df.empty:
            disp_df = model_picks_df.sort_values("Date", ascending=False).copy()
            # Highlight columns used for grading
            grade_cols = ["Date","Side","Market","ML","Book","Model_Prob","Market_Prob","Edge_Pct","Result","F5_Score"]
            show_cols  = [c for c in grade_cols if c in disp_df.columns]
            st.dataframe(disp_df[show_cols], hide_index=True, use_container_width=True)
            st.download_button("📥 Download CSV",
                model_picks_df.to_csv(index=False).encode(),"model_picks.csv","text/csv")
        else:
            st.info("No picks tracked yet — signals ≥60% model prob will appear here on game days.")
