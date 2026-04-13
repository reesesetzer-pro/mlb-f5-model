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
BOOKS   = "draftkings,fanduel,betmgm,williamhill_us,espnbet,pinnacle"
REGIONS = "us,us2,eu"
BOOK_LABELS = {
    "draftkings":     "DraftKings",
    "fanduel":        "FanDuel",
    "betmgm":         "BetMGM",
    "williamhill_us": "Caesars",
    "espnbet":        "theScore",
    "pinnacle":       "Pinnacle",
}
# Pinnacle is the reference/sharp book — used for market probability calibration only.
# Recreational books are where we display the best available price for wagering.
REC_BOOKS = {"draftkings","fanduel","betmgm","williamhill_us","espnbet"}
TRACKER_FILE      = "bet_tracker.csv"
SP_FILE           = "sp_data.csv"
CACHE_FILE        = "game_cache.json"
MODEL_PICKS_FILE  = "model_picks.csv"

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

def calc_nrfi_prob(away_sp_score, home_sp_score, away_lu, home_lu, pf, ump_k):
    """
    Estimate P(NRFI) — no run by either team in the 1st inning.
    Base ≈ 0.52 (market typically prices NRFI at -115 to -140).
    Stronger SPs / weaker lineups / K-heavy umps push probability up.
    """
    base    = 0.52
    avg_sp  = ((away_sp_score or 50) + (home_sp_score or 50)) / 2
    avg_lu  = ((away_lu or 50)       + (home_lu or 50))       / 2
    sp_adj  =  (avg_sp - 50) / 200        # ±0.10 for elite / poor SP
    lu_adj  = -(avg_lu - 50) / 300        # strong lineups hurt NRFI
    ump_adj =  (ump_k  or 0) * 0.04       # K-heavy ump → fewer runs
    pf_adj  = -(pf - 1.0)   * 0.25       # hitter parks → fewer NRFI
    return round(max(0.35, min(0.72, base + sp_adj + lu_adj + ump_adj + pf_adj)), 4)

def calc_fi_u15_prob(away_sp_score, home_sp_score, away_lu, home_lu, pf, ump_k):
    """
    Estimate P(1st inning total ≤ 1.5) — at most 1 combined run.
    Base ≈ 0.76 (U1.5 1st inning typically priced -220 to -280).
    """
    base    = 0.76
    avg_sp  = ((away_sp_score or 50) + (home_sp_score or 50)) / 2
    avg_lu  = ((away_lu or 50)       + (home_lu or 50))       / 2
    sp_adj  =  (avg_sp - 50) / 300
    lu_adj  = -(avg_lu - 50) / 400
    ump_adj =  (ump_k  or 0) * 0.03
    pf_adj  = -(pf - 1.0)   * 0.20
    return round(max(0.60, min(0.90, base + sp_adj + lu_adj + ump_adj + pf_adj)), 4)

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

def auto_log_model_picks(signals, picks_df, min_model_prob=0.52):
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
        "📋 Today's Slate","🎯 Bet Signals","✏️ SP Input","📈 Bet Tracker","🏟️ Park Factors","📊 Model Performance"])
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
            if game_started:
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
                        st.caption(f"🎯 SP: {sp.get('name','TBD')}")
                        if sp.get('sp_score'): st.caption(f"Score: **{sp['sp_score']}**")
                        if sp.get('xfip'):     st.caption(f"xFIP: {sp['xfip']}")
                with c3:
                    st.markdown(f"### {time_et}")
                    pf_color = "🔴" if pf>1.04 else "🟡" if pf>1.01 else "🟢" if pf<0.97 else "⚪"
                    st.caption(f"{pf_color} Park: **{pf:.2f}x**")
                    if ump: st.caption(f"🧑‍⚖️ {ump.split()[-1]} ({ump_k:+.2f} K)")
                with c4:
                    sp = home_sp_data
                    if sp:
                        st.caption(f"🎯 SP: {sp.get('name','TBD')}")
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
            try:
                time_et = fmt_time_et(dt)
            except: time_et=""

            c_data  = cache_by_away.get(away, cache_by_home.get(home,{}))
            pf           = c_data.get("park_factor", 1.0)
            ump_k        = c_data.get("ump_k_boost", 0.0)
            ump_run_fac  = c_data.get("ump_run_factor", 1.0)
            ump_zone     = c_data.get("ump_zone_size", 1.0)
            away_lu      = c_data.get("away_lineup_score")
            home_lu      = c_data.get("home_lineup_score")
            away_sp      = c_data.get("away_sp", {})
            home_sp      = c_data.get("home_sp", {})

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

            # Reference probability — Pinnacle vig-free (sharpest) when available,
            # else vig-free from recreational book average.
            pin_ml = odds_data["ml"].get("pinnacle", {})
            if pin_ml.get("away") and pin_ml.get("home"):
                true_away, true_home = vig_free(pin_ml["away"], pin_ml["home"])
                using_pinnacle = True
            else:
                true_away, true_home = vig_free(
                    sum(away_mls_rec)/len(away_mls_rec), sum(home_mls_rec)/len(home_mls_rec))
                using_pinnacle = False
            if not true_away: continue

            sp_edge   = (eff_asp - eff_hsp) / 100 * w_sp
            lu_edge   = ((eff_away_lu - eff_home_lu) / 100 * w_lu)
            park_edge = (pf - 1.0) * w_park * -1
            away_kbb  = away_sp.get("k_bb_pct") or 10
            home_kbb  = home_sp.get("k_bb_pct") or 10
            ump_edge  = ump_k * ((away_kbb - home_kbb)/100) * w_ump

            model_away = max(0.05, min(0.95, true_away + sp_edge + lu_edge + park_edge + ump_edge))
            model_home = 1 - model_away
            # mkt_p = Pinnacle vig-free if available (true market price), else one-sided rec book
            mkt_away = true_away if using_pinnacle else american_to_prob(best_away_ml)
            mkt_home = true_home if using_pinnacle else american_to_prob(best_home_ml)
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
                        "sharp_ref":using_pinnacle,
                        "sp_score":sp_s,"lu_score":lu_s,"eff_lu":eff_lu,
                        "matchup_score":matchup_s,"platoon_adv":plat_adv,
                        "opp_hand": home_sp.get("hand","R") if side=="Away" else away_sp.get("hand","R"),
                        "form_score": away_sp.get("form_score",0) if side=="Away" else home_sp.get("form_score",0),
                        "days_rest":  away_sp.get("days_rest") if side=="Away" else home_sp.get("days_rest"),
                        "weather": wx,
                        "park_factor":pf,"ump_k":ump_k,"ump_zone":ump_zone,
                        "model_line":"","mkt_line":"",
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
                    # Use Pinnacle's total line if available (sharpest consensus)
                    pin_tot = odds_data["total"].get("pinnacle", {})
                    if pin_tot.get("over_line"):
                        consensus_total = round(float(pin_tot["over_line"]), 1)
                        tot_sharp_ref   = True
                    else:
                        consensus_total = round(sum(all_lines)/len(all_lines), 1)
                        tot_sharp_ref   = False
                    model_t = calc_model_total(eff_asp, eff_hsp, eff_away_lu, eff_home_lu, pf, ump_k,
                                           away_sp.get("era"), home_sp.get("era"),
                                           ump_run_fac, wx_wind_mult, wx_temp_mult)
                    over_p  = over_prob(model_t, consensus_total)
                    under_p = 1 - over_p

                    # Best price at rec books (where to bet)
                    best_over_bk  = max(total_books_rec, key=lambda b: odds_data["total"][b].get("over_price",-200) or -200)
                    best_under_bk = max(total_books_rec, key=lambda b: odds_data["total"][b].get("under_price",-200) or -200)
                    over_ml  = odds_data["total"][best_over_bk].get("over_price")  or -110
                    under_ml = odds_data["total"][best_under_bk].get("under_price") or -110
                    # mkt_p: use Pinnacle's price if available (efficient reference)
                    if pin_tot.get("over_price"):
                        mkt_over_p  = american_to_prob(pin_tot["over_price"])  or 0.524
                        mkt_under_p = 1 - mkt_over_p
                    else:
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
                                "sharp_ref":tot_sharp_ref,
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
                model_nrfi = calc_nrfi_prob(eff_asp, eff_hsp, eff_away_lu, eff_home_lu, pf, ump_k)
                model_yrfi = round(1 - model_nrfi, 4)
                model_u15  = calc_fi_u15_prob(eff_asp, eff_hsp, eff_away_lu, eff_home_lu, pf, ump_k)
                _fi_base   = {"game":game_tag,"time":time_et,
                              "away_abv":abv_away,"home_abv":abv_home,
                              "form_score":0,"days_rest":None,"weather":wx,
                              "matchup_score":0,"park_factor":pf,
                              "ump_k":ump_k,"ump_zone":ump_zone,"sharp_ref":False,
                              "sp_score":(eff_asp+eff_hsp)/2,
                              "lu_score":((away_lu or 50)+(home_lu or 50))/2}

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

        # ── DISPLAY ───────────────────────────────────────────────────────────
        if not signals:
            st.info("No signals found on today's slate with current data.")
        else:
            # Primary sort: model probability (most likely to hit), secondary: edge
            signals.sort(key=lambda x: (x["model_p"], x["edge"]), reverse=True)

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
                sharp_ref_txt = '<span class="metric-pill" style="border-color:#9c27b0;color:#9c27b0">PIN ref</span>' if s.get("sharp_ref") else ""
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
                    f'<span class="metric-pill" style="border-color:{edge_color};color:{edge_color}"><b>{edge_label}</b></span>',
                    f'<span class="metric-pill">+{ml_str} @ {s["book"]}</span>',
                    f'<span class="metric-pill">Mkt: {s["mkt_p"]*100:.1f}%</span>',
                    f'<span class="metric-pill">SP: {s["sp_score"]:.0f}{lu_txt}</span>',
                    matchup_txt,
                    form_txt,
                    weather_txt,
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
                    s1,s2,s3,s4,s5 = st.columns([1,1,1,1,2])
                    if s1.button("✅ WIN",  key=f"w{idx}", use_container_width=True):
                        tracker_df.at[idx,"Result"]="WIN"; save_tracker(tracker_df); st.rerun()
                    if s2.button("❌ LOSS", key=f"l{idx}", use_container_width=True):
                        tracker_df.at[idx,"Result"]="LOSS"; save_tracker(tracker_df); st.rerun()
                    if s3.button("➖ PUSH", key=f"p{idx}", use_container_width=True):
                        tracker_df.at[idx,"Result"]="PUSH"; save_tracker(tracker_df); st.rerun()
                    closing = s4.number_input("Closing ML", value=0, key=f"cl{idx}", label_visibility="collapsed")
                    score   = s5.text_input("F5 Score (e.g. 3-1)", key=f"sc{idx}", label_visibility="collapsed",
                                            placeholder="F5 score (optional)")
                    if closing:
                        try:
                            tracker_df.at[idx,"Closing_ML"] = closing
                            tracker_df.at[idx,"CLV"] = float(str(row["Bet_ML"]).replace("+","")) - closing
                            save_tracker(tracker_df)
                        except: pass
                    if score:
                        tracker_df.at[idx,"F5_Score"] = score; save_tracker(tracker_df)

            st.divider()

        # ── Full log ──────────────────────────────────────────────────────────
        st.subheader("📋 Full Log")
        display_cols = ["Date","Game","Bet_Side","Market","Book","Bet_ML",
                        "Model_Prob","Edge_Pct","Wager","Result","Profit_Loss","CLV","Notes"]
        show_cols = [c for c in display_cols if c in tracker_df.columns]
        st.dataframe(tracker_df[show_cols].sort_values("Date",ascending=False)
                     if not tracker_df.empty else tracker_df,
                     hide_index=True, use_container_width=True)
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
    st.dataframe(df.style.background_gradient(subset=["F5 Factor"],cmap="RdYlGn",
                 vmin=0.90,vmax=1.30), hide_index=True, use_container_width=True)
    st.caption("Factors updated annually. Coors Field (+28%) and Great American Ball Park (+12%) are the most significant outliers.")

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: MODEL PERFORMANCE
# ══════════════════════════════════════════════════════════════════════════════
elif page == "📊 Model Performance":
    st.title("📊 Model Performance & Learning")
    st.caption("All signals ≥52% model confidence are auto-tracked here. Settle results to train the model.")

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
        st.subheader("📋 Full Model Pick History")
        disp_df = model_picks_df.sort_values("Date", ascending=False) if not model_picks_df.empty else model_picks_df
        st.dataframe(disp_df, hide_index=True, use_container_width=True)
        if not model_picks_df.empty:
            st.download_button("📥 Download CSV",
                model_picks_df.to_csv(index=False).encode(),"model_picks.csv","text/csv")
