import streamlit as st
import requests
import pandas as pd
import json, os
from datetime import datetime, date

st.set_page_config(page_title="MLB F5 Model", page_icon="⚾", layout="wide",
                   initial_sidebar_state="expanded")

# ── CONSTANTS ─────────────────────────────────────────────────────────────────
API_KEY = "40cfbba84e52cd6da31272d4ac287966"
SPORT   = "baseball_mlb"
BOOKS   = "draftkings,fanduel,betmgm,williamhill_us,espnbet"
REGIONS = "us,us2"
BOOK_LABELS = {
    "draftkings":     "DraftKings",
    "fanduel":        "FanDuel",
    "betmgm":         "BetMGM",
    "williamhill_us": "Caesars",
    "espnbet":        "theScore",
}
TRACKER_FILE = "bet_tracker.csv"
SP_FILE      = "sp_data.csv"
CACHE_FILE   = "game_cache.json"

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
  .block-container{padding-top:1rem}
  .game-card{background:#1a2b4a;border-radius:12px;padding:16px;margin-bottom:12px;
             border:1px solid #2e75b6}
  .bet-strong{background:#0d2b1a;border-left:4px solid #00c853;border-radius:8px;
              padding:14px;margin:6px 0}
  .bet-moderate{background:#2b2000;border-left:4px solid #ffd600;border-radius:8px;
                padding:14px;margin:6px 0}
  .no-edge{background:#1a1a2e;border-left:4px solid #444;border-radius:8px;
           padding:14px;margin:6px 0}
  .metric-pill{background:#1a2b4a;border-radius:20px;padding:4px 12px;
               display:inline-block;margin:2px;font-size:0.85rem}
  .park-badge{background:#2e3f55;border-radius:6px;padding:2px 8px;font-size:0.8rem}
  .ump-badge{background:#3a2a4a;border-radius:6px;padding:2px 8px;font-size:0.8rem}
  .lineup-badge{background:#1a3a2a;border-radius:6px;padding:2px 8px;font-size:0.8rem}
  div[data-testid="stMetricValue"]{font-size:1.5rem}
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

# ── DATA FETCHING ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def fetch_games():
    url = f"https://api.the-odds-api.com/v4/sports/{SPORT}/odds/"
    params = {"apiKey":API_KEY,"regions":"us","markets":"h2h","oddsFormat":"american"}
    try:
        r = requests.get(url, params=params, timeout=15); r.raise_for_status()
        data = r.json(); today = datetime.utcnow().date()
        return [g for g in data
                if datetime.strptime(g["commence_time"],"%Y-%m-%dT%H:%M:%SZ").date()==today], None
    except Exception as e: return [], str(e)

@st.cache_data(ttl=300)
def fetch_f5(event_id, away, home):
    url = f"https://api.the-odds-api.com/v4/sports/{SPORT}/events/{event_id}/odds"
    params = {"apiKey":API_KEY,"regions":REGIONS,"markets":"h2h_1st_5_innings,totals_1st_5_innings",
              "bookmakers":BOOKS,"oddsFormat":"american"}
    result = {"ml":{},"total":{}}
    try:
        r = requests.get(url, params=params, timeout=15); r.raise_for_status()
        for bm in r.json().get("bookmakers",[]):
            k = bm["key"]
            for mkt in bm.get("markets",[]):
                if mkt["key"] == "h2h_1st_5_innings":
                    o = {x["name"]:x["price"] for x in mkt["outcomes"]}
                    result["ml"][k] = {"away":o.get(away),"home":o.get(home)}
                elif mkt["key"] == "totals_1st_5_innings":
                    for o in mkt["outcomes"]:
                        if o["name"]=="Over": result["total"][k]=o.get("point")
    except: pass
    return result

def load_sp_data():
    if os.path.exists(SP_FILE): return pd.read_csv(SP_FILE)
    return pd.DataFrame(columns=["Team","Pitcher","Hand","xFIP","K_BB_pct","Hard_Hit_pct","SP_Score"])

def save_sp_data(df): df.to_csv(SP_FILE, index=False)

def calc_sp_score(xfip, kbb, hh=None):
    try:
        s = (100-(xfip*12))*0.40 + (kbb*100*0.35)
        if hh: s += (30-hh*100)*0.25
        return round(max(0,min(100,s)),1)
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

# ── SIDEBAR ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("# ⚾ MLB F5 Model")
    st.caption(f"Updated: {datetime.now().strftime('%I:%M %p')}")
    st.divider()
    st.subheader("💰 Bankroll Settings")
    bankroll   = st.number_input("Bankroll ($)", value=1000, step=100, min_value=100)
    kelly_frac = st.slider("Kelly Fraction", 0.1, 1.0, 0.25, 0.05)
    min_edge   = st.slider("Min Edge (%)", 1, 10, 3) / 100
    max_pct    = st.slider("Max Bet % Bankroll", 1, 10, 5) / 100
    st.divider()
    st.subheader("🔧 Model Weights")
    w_sp   = st.slider("SP Score weight",      0.1, 0.8, 0.45, 0.05)
    w_lu   = st.slider("Lineup Quality weight",0.1, 0.6, 0.30, 0.05)
    w_park = st.slider("Park Factor weight",   0.0, 0.3, 0.15, 0.05)
    w_ump  = st.slider("Ump Tendency weight",  0.0, 0.2, 0.10, 0.05)
    st.divider()
    if st.button("🔄 Refresh Odds", use_container_width=True):
        st.cache_data.clear(); st.rerun()
    page = st.radio("Navigate", [
        "📋 Today's Slate","🎯 Bet Signals","✏️ SP Input","📈 Bet Tracker","🏟️ Park Factors"])

# ── LOAD DATA ─────────────────────────────────────────────────────────────────
games, err = fetch_games()
sp_df       = load_sp_data()
tracker_df  = load_tracker()
cache       = load_cache()

# Build cache lookup by team name
cache_by_away = {g["away_team"]: g for g in cache}
cache_by_home = {g["home_team"]: g for g in cache}

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: TODAY'S SLATE
# ══════════════════════════════════════════════════════════════════════════════
if page == "📋 Today's Slate":
    st.title(f"📋 Today's F5 Slate — {date.today().strftime('%A, %B %d, %Y')}")
    if err: st.error(f"API Error: {err}")
    elif not games: st.info("⚾ No games today. Check back on a game day!")
    else:
        st.success(f"✅ {len(games)} games on today's slate")
        for game in games:
            away = game["away_team"]; home = game["home_team"]
            abv_away = get_abv(away);  abv_home = get_abv(home)
            odds_data = fetch_f5(game["id"], away, home)
            try:
                dt = datetime.strptime(game["commence_time"],"%Y-%m-%dT%H:%M:%SZ")
                time_et = dt.strftime("%#I:%M %p") + " ET"
            except: time_et = ""

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
                tots = [(BOOK_LABELS.get(b,b), odds_data["total"][b])
                        for b in BOOK_LABELS if b in odds_data["total"] and odds_data["total"][b]]
                if tots:
                    st.caption("**F5 Totals:** " + " | ".join([f"{bn}: **{t}**" for bn,t in tots]))
                    avg_t = sum(t for _,t in tots)/len(tots)
                    st.caption(f"Consensus F5 Total: **{avg_t:.2f}**")

                st.markdown('</div>', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: BET SIGNALS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🎯 Bet Signals":
    st.title("🎯 Bet Signals — Edge Rankings")
    if not games: st.info("No games today.")
    else:
        signals = []
        for game in games:
            away = game["away_team"]; home = game["home_team"]
            abv_away = get_abv(away);  abv_home = get_abv(home)
            odds_data = fetch_f5(game["id"], away, home)
            try:
                dt = datetime.strptime(game["commence_time"],"%Y-%m-%dT%H:%M:%SZ")
                time_et = dt.strftime("%#I:%M %p")+" ET"
            except: time_et=""

            # Enriched cache data
            c_data   = cache_by_away.get(away, cache_by_home.get(home,{}))
            pf       = c_data.get("park_factor", 1.0)
            ump_k    = c_data.get("ump_k_boost", 0.0)
            away_lu  = c_data.get("away_lineup_score")
            home_lu  = c_data.get("home_lineup_score")
            away_sp  = c_data.get("away_sp",{})
            home_sp  = c_data.get("home_sp",{})

            away_mls = [odds_data["ml"][b]["away"] for b in BOOK_LABELS
                        if b in odds_data["ml"] and odds_data["ml"][b]["away"]]
            home_mls = [odds_data["ml"][b]["home"] for b in BOOK_LABELS
                        if b in odds_data["ml"] and odds_data["ml"][b]["home"]]
            if not away_mls or not home_mls: continue

            best_away_ml = max(away_mls); best_home_ml = max(home_mls)
            best_away_bk = max((b for b in BOOK_LABELS if b in odds_data["ml"] and odds_data["ml"][b]["away"]),
                               key=lambda b: odds_data["ml"][b]["away"])
            best_home_bk = max((b for b in BOOK_LABELS if b in odds_data["ml"] and odds_data["ml"][b]["home"]),
                               key=lambda b: odds_data["ml"][b]["home"])

            # Vig-free market probs
            true_away, true_home = vig_free(
                sum(away_mls)/len(away_mls), sum(home_mls)/len(home_mls))
            if not true_away: continue

            # SP score adjustment
            asp = away_sp.get("sp_score") or 50
            hsp = home_sp.get("sp_score") or 50
            sp_edge = (asp - hsp) / 100 * w_sp

            # Lineup adjustment
            lu_edge = 0.0
            if away_lu and home_lu:
                lu_edge = (away_lu - home_lu) / 100 * w_lu

            # Park factor adjustment (high PF hurts away pitcher slightly)
            park_edge = (pf - 1.0) * w_park * -1  # high PF = slight home advantage

            # Ump K% adjustment (high K ump = helps SP with better K-BB%)
            away_kbb = away_sp.get("k_bb_pct") or 10
            home_kbb = home_sp.get("k_bb_pct") or 10
            ump_edge = ump_k * ((away_kbb - home_kbb)/100) * w_ump

            model_away = max(0.05, min(0.95, true_away + sp_edge + lu_edge + park_edge + ump_edge))
            model_home = 1 - model_away

            mkt_away = american_to_prob(best_away_ml)
            mkt_home = american_to_prob(best_home_ml)

            away_edge = model_away - mkt_away
            home_edge = model_home - mkt_home

            for side, edge, ml, bk, model_p, mkt_p, sp_s, lu_s, team, abv in [
                ("Away", away_edge, best_away_ml, best_away_bk, model_away, mkt_away, asp, away_lu, away, abv_away),
                ("Home", home_edge, best_home_ml, best_home_bk, model_home, mkt_home, hsp, home_lu, home, abv_home),
            ]:
                if edge > 0:
                    k = kelly(edge, ml, bankroll, kelly_frac, max_pct)
                    signals.append({
                        "game": f"{away} @ {home}", "time": time_et,
                        "team": team, "abv": abv, "side": side,
                        "edge": edge, "ml": ml, "book": BOOK_LABELS.get(bk,bk),
                        "model_p": model_p, "mkt_p": mkt_p,
                        "kelly": k, "sp_score": sp_s, "lu_score": lu_s,
                        "park_factor": pf, "ump_k": ump_k,
                        "away_sp": away_sp, "home_sp": home_sp,
                    })

        if not signals:
            st.info("No positive edges found on today's slate with current data.")
        else:
            signals.sort(key=lambda x: x["edge"], reverse=True)
            strong   = [s for s in signals if s["edge"] >= 0.05]
            moderate = [s for s in signals if 0.03 <= s["edge"] < 0.05]

            m1,m2,m3,m4 = st.columns(4)
            m1.metric("Total Edges",    len(signals))
            m2.metric("🟢 Strong (>5%)", len(strong))
            m3.metric("🟡 Moderate (3-5%)", len(moderate))
            m4.metric("Total Rec. Wagers", f"${sum(s['kelly'] for s in signals if s['edge']>=min_edge):,.0f}")
            st.divider()

            for s in [s for s in signals if s["edge"] >= min_edge]:
                css = "bet-strong" if s["edge"]>=0.05 else "bet-moderate"
                badge = "🟢 STRONG EDGE" if s["edge"]>=0.05 else "🟡 MODERATE EDGE"
                lu_txt = f" | LU: {s['lu_score']:.0f}/100" if s['lu_score'] else ""
                pf_txt = f"Park: {s['park_factor']:.2f}x"
                ump_txt = f"Ump K: {s['ump_k']:+.2f}" if s['ump_k'] else ""

                st.markdown(f"""
                <div class="{css}">
                  <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">
                    <img src="{logo_url(s['abv'])}" width="40" style="border-radius:50%"/>
                    <div>
                      <strong>{badge} — {s['team']} ({s['side']})</strong><br>
                      <small style="color:#8ab4d4">{s['game']} | {s['time']}</small>
                    </div>
                  </div>
                  <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:8px">
                    <span class="metric-pill">📊 Edge: <b>{s['edge']*100:.1f}%</b></span>
                    <span class="metric-pill">💰 Best: <b>{'+' if s['ml']>0 else ''}{s['ml']}</b> @ {s['book']}</span>
                    <span class="metric-pill">🎯 Model: <b>{s['model_p']*100:.1f}%</b></span>
                    <span class="metric-pill">📉 Market: <b>{s['mkt_p']*100:.1f}%</b></span>
                    <span class="metric-pill">⚾ SP Score: <b>{s['sp_score']:.0f}</b>{lu_txt}</span>
                    <span class="park-badge">{pf_txt}</span>
                    {f'<span class="ump-badge">{ump_txt}</span>' if ump_txt else ""}
                  </div>
                  <strong>✅ Rec. Wager: ${s['kelly']:,.2f}</strong>
                </div>
                """, unsafe_allow_html=True)

            # Log bet
            st.divider(); st.subheader("➕ Log a Bet")
            with st.form("log_bet"):
                eligible = [s for s in signals if s["edge"] >= min_edge]
                sel = st.selectbox("Select Signal",
                    [f"{s['team']} ({s['side']}) — {s['game']}" for s in eligible])
                wager = st.number_input("Wager ($)", min_value=1.0, value=10.0)
                notes = st.text_input("Notes")
                if st.form_submit_button("Log Bet") and eligible:
                    idx = [f"{s['team']} ({s['side']}) — {s['game']}" for s in eligible].index(sel)
                    s = eligible[idx]
                    new = {"Date":date.today().strftime("%m/%d/%Y"),"Game":s["game"],
                           "Bet_Side":f"{s['team']} ({s['side']})","Market":"F5 ML",
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
    st.title("📈 Season Bet Tracker & P&L")
    if not tracker_df.empty:
        settled = tracker_df[tracker_df["Result"].isin(["WIN","LOSS","PUSH"])]
        wins = len(settled[settled["Result"]=="WIN"])
        losses = len(settled[settled["Result"]=="LOSS"])
        pending = len(tracker_df[tracker_df["Result"]=="PENDING"])
        tracker_df["Profit_Loss"] = tracker_df.apply(
            lambda r: calc_pnl(r) if r["Result"] in ["WIN","LOSS","PUSH"] else None, axis=1)
        net = tracker_df["Profit_Loss"].sum()
        wag = tracker_df["Wager"].astype(float).sum()
        roi = (net/wag*100) if wag>0 else 0
        c1,c2,c3,c4,c5,c6 = st.columns(6)
        c1.metric("Total Bets",  len(tracker_df))
        c2.metric("Record",      f"{wins}-{losses}")
        c3.metric("Pending",     pending)
        c4.metric("Net P&L",     f"${net:+,.2f}")
        c5.metric("Wagered",     f"${wag:,.2f}")
        c6.metric("ROI",         f"{roi:+.1f}%")
        if wins+losses>0:
            st.progress(wins/(wins+losses), text=f"Win Rate: {wins/(wins+losses)*100:.1f}%")
        st.divider()

    pending_df = tracker_df[tracker_df["Result"]=="PENDING"] if not tracker_df.empty else pd.DataFrame()
    st.subheader("✏️ Update Results")
    if pending_df.empty: st.caption("No pending bets.")
    else:
        for idx,row in pending_df.iterrows():
            with st.expander(f"{row['Date']} — {row['Bet_Side']} @ {row['Bet_ML']}"):
                r1,r2,r3 = st.columns(3)
                result  = r1.selectbox("Result",["PENDING","WIN","LOSS","PUSH"],key=f"r{idx}")
                closing = r2.number_input("Closing ML",value=0,key=f"c{idx}")
                score   = r3.text_input("F5 Score",key=f"s{idx}")
                if st.button("Update",key=f"u{idx}"):
                    tracker_df.at[idx,"Result"]=result
                    tracker_df.at[idx,"Closing_ML"]=closing if closing else ""
                    tracker_df.at[idx,"F5_Score"]=score
                    if closing and row["Bet_ML"]:
                        tracker_df.at[idx,"CLV"]=float(str(row["Bet_ML"]).replace("+",""))-closing
                    save_tracker(tracker_df); st.success("Updated!"); st.rerun()

    st.divider(); st.subheader("📋 Full Log")
    if tracker_df.empty: st.info("No bets logged yet.")
    else:
        st.dataframe(tracker_df, hide_index=True, use_container_width=True)
        st.download_button("📥 Download CSV",
            tracker_df.to_csv(index=False).encode(),"f5_bets.csv","text/csv")

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
