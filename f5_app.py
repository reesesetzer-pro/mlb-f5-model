import streamlit as st
import requests
import pandas as pd
from datetime import datetime, date
import json
import os

# ── PAGE CONFIG ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="MLB F5 Model",
    page_icon="⚾",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CONSTANTS ─────────────────────────────────────────────────────────────────
API_KEY    = "40cfbba84e52cd6da31272d4ac287966"
SPORT      = "baseball_mlb"
BOOKS      = "draftkings,fanduel,betmgm,espnbet"
REGIONS    = "us,us2"
BOOK_LABELS = {
    "draftkings": "DraftKings",
    "fanduel":    "FanDuel",
    "betmgm":     "BetMGM",
    "espnbet":    "theScore",
}
TRACKER_FILE = "bet_tracker.csv"
SP_FILE      = "sp_data.csv"

# ── CUSTOM CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main { background-color: #0f1923; }
    .block-container { padding-top: 1rem; }
    h1 { color: #FFFFFF; font-family: Arial; }
    h2 { color: #2E75B6; font-family: Arial; }
    h3 { color: #C9A84C; font-family: Arial; }
    .metric-card {
        background: #1a2b4a;
        border-radius: 8px;
        padding: 16px;
        border-left: 4px solid #2E75B6;
        margin-bottom: 8px;
    }
    .bet-strong {
        background: #1e4d2b;
        border-left: 4px solid #00c853;
        border-radius: 6px;
        padding: 12px;
        margin: 4px 0;
    }
    .bet-moderate {
        background: #4d3b00;
        border-left: 4px solid #ffd600;
        border-radius: 6px;
        padding: 12px;
        margin: 4px 0;
    }
    .no-edge {
        background: #1a1a2e;
        border-left: 4px solid #444;
        border-radius: 6px;
        padding: 12px;
        margin: 4px 0;
    }
    .stMetric label { color: #8ab4d4 !important; }
    .stMetric value { color: #ffffff !important; }
    div[data-testid="stMetricValue"] { color: #ffffff; font-size: 1.6rem; }
    div[data-testid="stMetricLabel"] { color: #8ab4d4; }
</style>
""", unsafe_allow_html=True)

# ── HELPERS ───────────────────────────────────────────────────────────────────
def american_to_prob(odds):
    """Convert American odds to implied probability."""
    try:
        odds = float(odds)
        if odds > 0:
            return 100 / (odds + 100)
        else:
            return -odds / (-odds + 100)
    except:
        return None

def prob_to_american(prob):
    """Convert probability to American odds."""
    try:
        if prob >= 0.5:
            return round(-prob / (1 - prob) * 100)
        else:
            return round((1 - prob) / prob * 100)
    except:
        return None

def kelly_bet(edge, odds, bankroll, kelly_fraction=0.25, max_pct=0.05):
    """Calculate Kelly bet size."""
    try:
        if odds > 0:
            b = odds / 100
        else:
            b = 100 / abs(odds)
        p = edge + american_to_prob(odds)
        q = 1 - p
        kelly_full = (b * p - q) / b
        kelly_sized = kelly_full * kelly_fraction
        max_bet = bankroll * max_pct
        return round(min(kelly_sized * bankroll, max_bet), 2)
    except:
        return 0

def vig_free_prob(away_odds, home_odds):
    """Remove vig to get true probabilities."""
    try:
        p_away = american_to_prob(away_odds)
        p_home = american_to_prob(home_odds)
        total = p_away + p_home
        return p_away / total, p_home / total
    except:
        return None, None

# ── DATA FETCHING ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)  # cache for 5 minutes
def fetch_todays_games():
    """Step 1: Get today's game IDs."""
    url = f"https://api.the-odds-api.com/v4/sports/{SPORT}/odds/"
    params = {
        "apiKey":     API_KEY,
        "regions":    "us",
        "markets":    "h2h",
        "oddsFormat": "american",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        today = datetime.utcnow().date()
        games = []
        for g in data:
            start = datetime.strptime(g["commence_time"], "%Y-%m-%dT%H:%M:%SZ")
            if start.date() == today:
                games.append({
                    "id":       g["id"],
                    "away":     g["away_team"],
                    "home":     g["home_team"],
                    "commence": start,
                })
        return games, None
    except Exception as e:
        return [], str(e)

@st.cache_data(ttl=300)
def fetch_f5_odds(event_id, away, home):
    """Step 2: Get F5 odds for a specific game."""
    url = f"https://api.the-odds-api.com/v4/sports/{SPORT}/events/{event_id}/odds"
    params = {
        "apiKey":      API_KEY,
        "regions":     REGIONS,
        "markets":     "h2h_1st_5_innings,totals_1st_5_innings",
        "bookmakers":  BOOKS,
        "oddsFormat":  "american",
    }
    result = {
        "ml":    {},
        "total": {},
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        for bm in data.get("bookmakers", []):
            key = bm["key"]
            for market in bm.get("markets", []):
                if market["key"] == "h2h_1st_5_innings":
                    outcomes = {o["name"]: o["price"] for o in market["outcomes"]}
                    result["ml"][key] = {
                        "away": outcomes.get(away),
                        "home": outcomes.get(home),
                    }
                elif market["key"] == "totals_1st_5_innings":
                    for o in market["outcomes"]:
                        if o["name"] == "Over":
                            result["total"][key] = o.get("point")
        return result
    except:
        return result

# ── SP DATA MANAGEMENT ────────────────────────────────────────────────────────
def load_sp_data():
    if os.path.exists(SP_FILE):
        return pd.read_csv(SP_FILE)
    return pd.DataFrame(columns=["Team", "Pitcher", "Hand", "xFIP", "K_BB_pct", "Hard_Hit_pct", "SP_Score"])

def save_sp_data(df):
    df.to_csv(SP_FILE, index=False)

def calc_sp_score(xfip, k_bb_pct, hard_hit_pct=None):
    try:
        score = (100 - (xfip * 12)) * 0.50 + (k_bb_pct * 100 * 0.35)
        if hard_hit_pct:
            score += (30 - hard_hit_pct * 100) * 0.15
        return round(max(0, min(100, score)), 1)
    except:
        return None

# ── BET TRACKER ───────────────────────────────────────────────────────────────
def load_tracker():
    if os.path.exists(TRACKER_FILE):
        return pd.read_csv(TRACKER_FILE)
    return pd.DataFrame(columns=[
        "Date", "Game", "Bet_Side", "Market", "Book",
        "Bet_ML", "Model_Prob", "Market_Implied", "Edge_Pct",
        "Wager", "F5_Score", "Result", "Profit_Loss", "Closing_ML", "CLV", "Notes"
    ])

def save_tracker(df):
    df.to_csv(TRACKER_FILE, index=False)

def calc_pnl(row):
    try:
        if row["Result"] == "WIN":
            odds = float(row["Bet_ML"])
            wager = float(row["Wager"])
            if odds > 0:
                return round(wager * odds / 100, 2)
            else:
                return round(wager * 100 / abs(odds), 2)
        elif row["Result"] == "LOSS":
            return -float(row["Wager"])
        elif row["Result"] == "PUSH":
            return 0
        return None
    except:
        return None

# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/commons/thumb/a/a6/Major_League_Baseball_logo.svg/200px-Major_League_Baseball_logo.svg.png", width=120)
    st.title("⚾ MLB F5 Model")
    st.caption(f"Last refreshed: {datetime.now().strftime('%I:%M %p')}")

    st.markdown("---")
    st.subheader("💰 Bankroll Settings")
    bankroll    = st.number_input("Bankroll ($)", value=1000, step=100, min_value=100)
    kelly_frac  = st.slider("Kelly Fraction", 0.1, 1.0, 0.25, 0.05,
                            help="0.25 = Quarter Kelly (recommended)")
    min_edge    = st.slider("Min Edge to Show (%)", 1, 10, 3) / 100
    max_bet_pct = st.slider("Max Bet % of Bankroll", 1, 10, 5) / 100

    st.markdown("---")
    st.subheader("🔄 Data")
    if st.button("🔄 Refresh Odds", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Auto-refreshes every 5 minutes")

    st.markdown("---")
    page = st.radio("Navigate", [
        "📋 Today's Slate",
        "🎯 Bet Signals",
        "🎯 SP Input",
        "📈 Bet Tracker",
    ])

# ══════════════════════════════════════════════════════════════════════════════
# FETCH DATA
# ══════════════════════════════════════════════════════════════════════════════
games, fetch_error = fetch_todays_games()
sp_df = load_sp_data()
tracker_df = load_tracker()

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: TODAY'S SLATE
# ══════════════════════════════════════════════════════════════════════════════
if page == "📋 Today's Slate":
    st.title(f"📋 Today's F5 Slate — {date.today().strftime('%A, %B %d, %Y')}")

    if fetch_error:
        st.error(f"API Error: {fetch_error}")
    elif not games:
        st.info("⚾ No games found for today. Check back on a game day!")
    else:
        st.success(f"✅ {len(games)} games on today's slate")

        for game in games:
            odds_data = fetch_f5_odds(game["id"], game["away"], game["home"])
            time_et   = game["commence"].strftime("%#I:%M %p") + " ET"

            with st.expander(f"⚾ **{game['away']}** @ **{game['home']}** — {time_et}", expanded=True):
                col1, col2 = st.columns(2)

                # Away ML
                with col1:
                    st.markdown(f"**🛫 {game['away']} (Away)**")
                    ml_data = []
                    for book_key, book_name in BOOK_LABELS.items():
                        if book_key in odds_data["ml"] and odds_data["ml"][book_key]["away"]:
                            ml = odds_data["ml"][book_key]["away"]
                            ml_data.append({"Book": book_name, "F5 ML": f"{'+' if ml > 0 else ''}{ml}"})
                    if ml_data:
                        ml_df = pd.DataFrame(ml_data)
                        # highlight best line
                        away_vals = [odds_data["ml"][b]["away"] for b in BOOK_LABELS if b in odds_data["ml"] and odds_data["ml"][b]["away"]]
                        best_away = max(away_vals) if away_vals else None
                        st.dataframe(ml_df, hide_index=True, use_container_width=True)
                        if best_away:
                            st.markdown(f"🟢 **Best Away Line: {'+' if best_away > 0 else ''}{best_away}**")
                    else:
                        st.caption("No F5 lines available yet")

                # Home ML
                with col2:
                    st.markdown(f"**🏠 {game['home']} (Home)**")
                    ml_data_h = []
                    for book_key, book_name in BOOK_LABELS.items():
                        if book_key in odds_data["ml"] and odds_data["ml"][book_key]["home"]:
                            ml = odds_data["ml"][book_key]["home"]
                            ml_data_h.append({"Book": book_name, "F5 ML": f"{'+' if ml > 0 else ''}{ml}"})
                    if ml_data_h:
                        st.dataframe(pd.DataFrame(ml_data_h), hide_index=True, use_container_width=True)
                        home_vals = [odds_data["ml"][b]["home"] for b in BOOK_LABELS if b in odds_data["ml"] and odds_data["ml"][b]["home"]]
                        best_home = max(home_vals) if home_vals else None
                        if best_home:
                            st.markdown(f"🟢 **Best Home Line: {'+' if best_home > 0 else ''}{best_home}**")
                    else:
                        st.caption("No F5 lines available yet")

                # F5 Totals
                st.markdown("**📊 F5 Totals (Over/Under)**")
                total_data = []
                for book_key, book_name in BOOK_LABELS.items():
                    if book_key in odds_data["total"] and odds_data["total"][book_key]:
                        total_data.append({"Book": book_name, "F5 Total": odds_data["total"][book_key]})
                if total_data:
                    tot_df = pd.DataFrame(total_data)
                    st.dataframe(tot_df, hide_index=True, use_container_width=True)
                    totals = [odds_data["total"][b] for b in BOOK_LABELS if b in odds_data["total"]]
                    if totals:
                        st.caption(f"Consensus F5 Total: **{round(sum(totals)/len(totals), 2)}**")
                else:
                    st.caption("No F5 totals available yet")

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: BET SIGNALS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🎯 Bet Signals":
    st.title("🎯 Bet Signals & Edge Rankings")

    if not games:
        st.info("No games today.")
    elif sp_df.empty:
        st.warning("⚠️ No SP data loaded yet. Go to **SP Input** tab to enter pitcher stats first.")
    else:
        signals = []

        for game in games:
            odds_data = fetch_f5_odds(game["id"], game["away"], game["home"])
            time_et   = game["commence"].strftime("%#I:%M %p") + " ET"

            away_sp = sp_df[sp_df["Team"].str.contains(
                game["away"].split()[-1], case=False, na=False)]
            home_sp = sp_df[sp_df["Team"].str.contains(
                game["home"].split()[-1], case=False, na=False)]

            away_score = float(away_sp["SP_Score"].values[0]) if not away_sp.empty else None
            home_score = float(home_sp["SP_Score"].values[0]) if not home_sp.empty else None

            # Get best ML lines
            away_mls = [odds_data["ml"][b]["away"] for b in BOOK_LABELS
                        if b in odds_data["ml"] and odds_data["ml"][b]["away"]]
            home_mls = [odds_data["ml"][b]["home"] for b in BOOK_LABELS
                        if b in odds_data["ml"] and odds_data["ml"][b]["home"]]

            if not away_mls or not home_mls:
                continue

            best_away_ml = max(away_mls)
            best_home_ml = max(home_mls)

            # Best book for each side
            best_away_book = max(
                [b for b in BOOK_LABELS if b in odds_data["ml"] and odds_data["ml"][b]["away"]],
                key=lambda b: odds_data["ml"][b]["away"]
            )
            best_home_book = max(
                [b for b in BOOK_LABELS if b in odds_data["ml"] and odds_data["ml"][b]["home"]],
                key=lambda b: odds_data["ml"][b]["home"]
            )

            # Vig-free consensus probabilities
            # Use average across books
            avg_away_ml = sum(away_mls) / len(away_mls)
            avg_home_ml = sum(home_mls) / len(home_mls)
            true_away_prob, true_home_prob = vig_free_prob(avg_away_ml, avg_home_ml)

            if not true_away_prob:
                continue

            # Model adjustment: blend market prob with SP score advantage
            if away_score and home_score:
                sp_diff = (away_score - home_score) / 100  # normalize
                # Away SP better = boost away win prob slightly
                model_away_prob = true_away_prob + (sp_diff * 0.08)
                model_home_prob = 1 - model_away_prob
            else:
                model_away_prob = true_away_prob
                model_home_prob = true_home_prob

            model_away_prob = max(0.05, min(0.95, model_away_prob))
            model_home_prob = 1 - model_away_prob

            # Edge vs best available line
            mkt_away_prob = american_to_prob(best_away_ml)
            mkt_home_prob = american_to_prob(best_home_ml)

            away_edge = model_away_prob - mkt_away_prob
            home_edge = model_home_prob - mkt_home_prob

            # Kelly sizing
            away_kelly = kelly_bet(away_edge, best_away_ml, bankroll, kelly_frac, max_bet_pct)
            home_kelly  = kelly_bet(home_edge,  best_home_ml,  bankroll, kelly_frac, max_bet_pct)

            for side, edge, ml, book, model_prob, mkt_prob, kelly in [
                ("Away", away_edge, best_away_ml, best_away_book,
                 model_away_prob, mkt_away_prob, away_kelly),
                ("Home", home_edge, best_home_ml, best_home_book,
                 model_home_prob, mkt_home_prob, home_kelly),
            ]:
                if edge > 0:
                    team = game["away"] if side == "Away" else game["home"]
                    signals.append({
                        "Game":         f"{game['away']} @ {game['home']}",
                        "Time":         time_et,
                        "Bet Side":     f"{'🛫' if side == 'Away' else '🏠'} {team}",
                        "Best ML":      f"{'+' if ml > 0 else ''}{ml}",
                        "Best Book":    BOOK_LABELS.get(book, book),
                        "Model Prob":   model_prob,
                        "Mkt Implied":  mkt_prob,
                        "Edge %":       edge,
                        "Kelly $":      kelly,
                        "Away SP":      away_score,
                        "Home SP":      home_score,
                    })

        if not signals:
            st.info("No edges found on today's slate with current SP data.")
        else:
            # Sort by edge descending
            signals.sort(key=lambda x: x["Edge %"], reverse=True)

            # Summary metrics
            strong  = [s for s in signals if s["Edge %"] >= 0.05]
            moderate = [s for s in signals if 0.03 <= s["Edge %"] < 0.05]

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Total Edges Found", len(signals))
            m2.metric("🟢 Strong (>5%)", len(strong))
            m3.metric("🟡 Moderate (3-5%)", len(moderate))
            m4.metric("Total Rec. Wagers", f"${sum(s['Kelly $'] for s in signals):,.0f}")

            st.markdown("---")

            for s in signals:
                edge_pct = s["Edge %"]
                css_class = "bet-strong" if edge_pct >= 0.05 else "bet-moderate" if edge_pct >= 0.03 else "no-edge"
                badge = "🟢 STRONG EDGE" if edge_pct >= 0.05 else "🟡 MODERATE EDGE"

                st.markdown(f"""
                <div class="{css_class}">
                    <strong>{badge} — {s['Bet Side']}</strong><br>
                    <small>{s['Game']} | {s['Time']}</small><br>
                    <strong>Best Line:</strong> {s['Best ML']} @ {s['Best Book']} &nbsp;|&nbsp;
                    <strong>Edge:</strong> {edge_pct*100:.1f}% &nbsp;|&nbsp;
                    <strong>Model Prob:</strong> {s['Model Prob']*100:.1f}% &nbsp;|&nbsp;
                    <strong>Mkt Implied:</strong> {s['Mkt Implied']*100:.1f}%<br>
                    <strong>✅ Rec. Wager: ${s['Kelly $']:,.2f}</strong> &nbsp;|&nbsp;
                    Away SP Score: {s['Away SP'] or 'N/A'} &nbsp;|&nbsp; Home SP Score: {s['Home SP'] or 'N/A'}
                </div>
                """, unsafe_allow_html=True)

            # Add to tracker button
            st.markdown("---")
            st.subheader("➕ Log a Bet")
            with st.form("log_bet"):
                sel_signal = st.selectbox("Select Signal",
                    [f"{s['Bet Side']} — {s['Game']}" for s in signals])
                wager_amt  = st.number_input("Wager ($)", min_value=1.0, value=10.0)
                bet_notes  = st.text_input("Notes (optional)")
                submit     = st.form_submit_button("Log Bet")

                if submit:
                    idx = [f"{s['Bet Side']} — {s['Game']}" for s in signals].index(sel_signal)
                    s = signals[idx]
                    new_row = {
                        "Date":           date.today().strftime("%m/%d/%Y"),
                        "Game":           s["Game"],
                        "Bet_Side":       s["Bet Side"],
                        "Market":         "F5 ML",
                        "Book":           s["Best Book"],
                        "Bet_ML":         s["Best ML"],
                        "Model_Prob":     round(s["Model Prob"] * 100, 1),
                        "Market_Implied": round(s["Mkt Implied"] * 100, 1),
                        "Edge_Pct":       round(s["Edge %"] * 100, 1),
                        "Wager":          wager_amt,
                        "F5_Score":       "",
                        "Result":         "PENDING",
                        "Profit_Loss":    "",
                        "Closing_ML":     "",
                        "CLV":            "",
                        "Notes":          bet_notes,
                    }
                    tracker_df = pd.concat(
                        [tracker_df, pd.DataFrame([new_row])], ignore_index=True)
                    save_tracker(tracker_df)
                    st.success(f"✅ Bet logged: {sel_signal}")

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: SP INPUT
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🎯 SP Input":
    st.title("🎯 Starting Pitcher Database")
    st.caption("Source: FanGraphs splits (F5 / 1-5 innings view) + Baseball Savant")

    st.info("Enter today's starting pitchers. xFIP and K-BB% are the two most critical inputs. "
            "SP Score auto-calculates from your inputs.")

    with st.form("sp_form"):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Away Starter**")
            away_team    = st.text_input("Away Team")
            away_pitcher = st.text_input("Away Pitcher Name")
            away_hand    = st.selectbox("Hand", ["R", "L"], key="away_hand")
            away_xfip    = st.number_input("xFIP", min_value=0.0, max_value=9.0, value=4.00,
                                            step=0.01, key="away_xfip")
            away_kbb     = st.number_input("K-BB%", min_value=0.0, max_value=0.50, value=0.10,
                                            step=0.01, key="away_kbb",
                                            help="Enter as decimal e.g. 0.12 = 12%")
            away_hh      = st.number_input("Hard Hit% (optional)", 0.0, 0.60, 0.35,
                                            step=0.01, key="away_hh",
                                            help="From Baseball Savant")

        with col2:
            st.markdown("**Home Starter**")
            home_team    = st.text_input("Home Team")
            home_pitcher = st.text_input("Home Pitcher Name")
            home_hand    = st.selectbox("Hand", ["R", "L"], key="home_hand")
            home_xfip    = st.number_input("xFIP", min_value=0.0, max_value=9.0, value=4.00,
                                            step=0.01, key="home_xfip")
            home_kbb     = st.number_input("K-BB%", 0.0, 0.50, 0.10,
                                            step=0.01, key="home_kbb")
            home_hh      = st.number_input("Hard Hit% (optional)", 0.0, 0.60, 0.35,
                                            step=0.01, key="home_hh")

        submitted = st.form_submit_button("💾 Save Pitchers", use_container_width=True)
        if submitted and away_team and home_team:
            new_rows = []
            for team, pitcher, hand, xfip, kbb, hh in [
                (away_team, away_pitcher, away_hand, away_xfip, away_kbb, away_hh),
                (home_team, home_pitcher, home_hand, home_xfip, home_kbb, home_hh),
            ]:
                score = calc_sp_score(xfip, kbb, hh)
                new_rows.append({
                    "Team": team, "Pitcher": pitcher, "Hand": hand,
                    "xFIP": xfip, "K_BB_pct": kbb, "Hard_Hit_pct": hh,
                    "SP_Score": score,
                })
            # Remove existing entries for these teams then append
            sp_df = sp_df[~sp_df["Team"].str.contains(
                f"{away_team}|{home_team}", case=False, na=False)]
            sp_df = pd.concat([sp_df, pd.DataFrame(new_rows)], ignore_index=True)
            save_sp_data(sp_df)
            st.success(f"✅ Saved: {away_pitcher} ({away_team}) & {home_pitcher} ({home_team})")
            st.rerun()

    # Show current SP database
    if not sp_df.empty:
        st.markdown("---")
        st.subheader("📊 Current SP Database")
        display_df = sp_df.copy()
        display_df["K_BB_pct"] = (display_df["K_BB_pct"] * 100).round(1).astype(str) + "%"
        display_df["Hard_Hit_pct"] = (display_df["Hard_Hit_pct"] * 100).round(1).astype(str) + "%"
        display_df.columns = ["Team", "Pitcher", "Hand", "xFIP", "K-BB%", "Hard Hit%", "SP Score"]
        st.dataframe(display_df, hide_index=True, use_container_width=True)

        if st.button("🗑️ Clear All SP Data"):
            save_sp_data(pd.DataFrame(columns=sp_df.columns))
            st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: BET TRACKER
# ══════════════════════════════════════════════════════════════════════════════
elif page == "📈 Bet Tracker":
    st.title("📈 Season Bet Tracker & P&L")

    # Summary metrics
    if not tracker_df.empty:
        settled = tracker_df[tracker_df["Result"].isin(["WIN", "LOSS", "PUSH"])]
        wins    = len(settled[settled["Result"] == "WIN"])
        losses  = len(settled[settled["Result"] == "LOSS"])
        pushes  = len(settled[settled["Result"] == "PUSH"])
        pending = len(tracker_df[tracker_df["Result"] == "PENDING"])

        tracker_df["Profit_Loss"] = tracker_df.apply(
            lambda r: calc_pnl(r) if r["Result"] in ["WIN","LOSS","PUSH"] else None, axis=1)
        net_pnl   = tracker_df["Profit_Loss"].sum()
        total_wag = tracker_df["Wager"].astype(float).sum()
        roi       = (net_pnl / total_wag * 100) if total_wag > 0 else 0

        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Total Bets",  len(tracker_df))
        c2.metric("Record",      f"{wins}-{losses}" + (f"-{pushes}" if pushes else ""))
        c3.metric("Pending",     pending)
        c4.metric("Net P&L",     f"${net_pnl:+,.2f}",
                  delta_color="normal" if net_pnl >= 0 else "inverse")
        c5.metric("Total Wagered", f"${total_wag:,.2f}")
        c6.metric("ROI",         f"{roi:+.1f}%",
                  delta_color="normal" if roi >= 0 else "inverse")

        st.markdown("---")

        # Win rate chart
        if wins + losses > 0:
            win_rate = wins / (wins + losses) * 100
            st.progress(win_rate / 100, text=f"Win Rate: {win_rate:.1f}%")

        st.markdown("---")

    # Update results
    st.subheader("✏️ Update Bet Results")
    pending_bets = tracker_df[tracker_df["Result"] == "PENDING"] if not tracker_df.empty else pd.DataFrame()

    if pending_bets.empty:
        st.caption("No pending bets.")
    else:
        for idx, row in pending_bets.iterrows():
            with st.expander(f"{row['Date']} — {row['Bet_Side']} ({row['Game']}) @ {row['Bet_ML']}"):
                col1, col2, col3 = st.columns(3)
                result     = col1.selectbox("Result", ["PENDING","WIN","LOSS","PUSH"],
                                             key=f"result_{idx}")
                closing_ml = col2.number_input("Closing ML", value=0, key=f"closing_{idx}")
                f5_score   = col3.text_input("F5 Final Score", key=f"score_{idx}")

                if st.button("Update", key=f"update_{idx}"):
                    tracker_df.at[idx, "Result"]     = result
                    tracker_df.at[idx, "Closing_ML"] = closing_ml if closing_ml != 0 else ""
                    tracker_df.at[idx, "F5_Score"]   = f5_score
                    if closing_ml and row["Bet_ML"]:
                        tracker_df.at[idx, "CLV"] = float(str(row["Bet_ML"]).replace("+","")) - closing_ml
                    save_tracker(tracker_df)
                    st.success("Updated!")
                    st.rerun()

    # Full log
    st.markdown("---")
    st.subheader("📋 Full Bet Log")
    if tracker_df.empty:
        st.info("No bets logged yet. Go to **Bet Signals** to log your first bet.")
    else:
        display = tracker_df.copy()
        st.dataframe(display, hide_index=True, use_container_width=True)

        col1, col2 = st.columns(2)
        with col1:
            csv = tracker_df.to_csv(index=False).encode()
            st.download_button("📥 Download Bet Log CSV", csv,
                               "f5_bet_tracker.csv", "text/csv")
        with col2:
            if st.button("🗑️ Clear All Bets", type="secondary"):
                save_tracker(pd.DataFrame(columns=tracker_df.columns))
                st.rerun()
