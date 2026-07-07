import hmac
from pathlib import Path

import pandas as pd
import streamlit as st
from st_keyup import st_keyup

import json

import config
import db
import enrichment
import export
import metrics

LOGO_PATH = Path(__file__).parent / "assets" / "logo.png"

st.set_page_config(page_title="Keymaster", page_icon=str(LOGO_PATH), layout="wide")

# ---------------- password gate ----------------
# Skips entirely if APP_PASSWORD isn't set (frictionless local dev). Deploying
# publicly without setting APP_PASSWORD leaves the app open to anyone with the
# link - see README before deploying.

if config.APP_PASSWORD and not st.session_state.get("authenticated"):
    st.title("Keymaster")
    entered = st.text_input("Password", type="password")
    if st.button("Log in"):
        if hmac.compare_digest(entered, config.APP_PASSWORD):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()

db.init_db()

# ---------------- header ----------------

logo_col, title_col = st.columns([1, 8])
with logo_col:
    st.image(str(LOGO_PATH))
with title_col:
    st.title("Keymaster")
    st.caption("Booking analytics for entertainment agency talent: revenue, expenses, comps, and history.")

badge_cols = st.columns(len(config.API_STATUS))
for col, (name, enabled) in zip(badge_cols, config.API_STATUS.items()):
    with col:
        if enabled:
            st.success(f"{name}: connected")
        else:
            st.info(f"{name}: not configured (add key to .env)")

st.divider()

# ---------------- pick or create a booking ----------------

def _autocomplete_field(label: str, session_key: str, suggest_fn, field_prefix: str) -> str:
    """A text field that suggests matches (from the DB and/or live APIs) as you
    type, via suggest_fn(current_text) -> list[str]. Clicking a suggestion
    fills the field. Lives outside any st.form so it can rerun on keystroke."""
    st.session_state.setdefault(session_key, "")
    value = st_keyup(label, value=st.session_state[session_key], debounce=300, key=f"{field_prefix}_keyup")
    st.session_state[session_key] = value
    if value:
        suggestions = suggest_fn(value)
        if suggestions:
            with st.container(horizontal=True):
                for suggestion in suggestions:
                    if st.button(suggestion, key=f"{field_prefix}_{suggestion}"):
                        st.session_state[session_key] = suggestion
                        st.rerun()
    return st.session_state[session_key]


tab_new_booking, tab_history = st.tabs(["New booking", "History"])

with tab_new_booking:
    _, center, _ = st.columns([1, 2, 1])
    with center:
        domain = st.segmented_control(
            "Domain", options=["music", "actor"], default="music", required=True, key="new_booking_domain"
        )
        talent_name = _autocomplete_field(
            "Talent name", "new_talent_name",
            lambda q: enrichment.suggest_talent_names(domain, q), "talent_sugg",
        )
        city = _autocomplete_field("City", "new_city", enrichment.suggest_city_names, "city_sugg")
        venue_name = _autocomplete_field(
            "Venue", "new_venue_name",
            lambda q: enrichment.suggest_venue_names(q, city=city), "venue_sugg",
        )

        with st.form("new_booking_form"):
            estimated_date = st.date_input("Estimated date")
            target_capacity = st.number_input("Target capacity", min_value=1, value=1000, step=50)
            budget = st.number_input("Budget ($)", min_value=0.0, value=50000.0, step=1000.0)

            with st.expander("Optional overrides"):
                override_price = st.number_input(
                    "Assumed ticket price ($, leave 0 to auto-resolve)", min_value=0.0, value=0.0, step=1.0
                )
                override_rate = st.slider(
                    "Assumed sell-through rate (0 to auto-resolve)", min_value=0.0, max_value=1.0, value=0.0
                )
            notes = st.text_area("Notes", height=80)

            submitted = st.form_submit_button("Save booking")
            if submitted:
                if not talent_name or not city:
                    st.error("Talent name and city are required.")
                else:
                    talent = db.get_or_create_talent(talent_name, domain)
                    talent = enrichment.enrich_talent_if_needed(talent)
                    performance_id = db.create_performance(
                        talent_id=talent["id"],
                        venue_name=venue_name,
                        city=city,
                        estimated_date=str(estimated_date),
                        target_capacity=int(target_capacity),
                        budget=float(budget),
                        assumed_ticket_price=override_price or None,
                        assumed_sell_through_rate=override_rate or None,
                        notes=notes,
                    )
                    st.session_state["selected_performance_id"] = performance_id
                    st.session_state["new_talent_name"] = ""
                    st.session_state["new_city"] = ""
                    st.session_state["new_venue_name"] = ""
                    st.success(f"Saved booking for {talent_name} in {city}.")
                    st.rerun()

with tab_history:
    st.subheader("Existing bookings")
    all_performances = db.list_all_performances()
    if not all_performances:
        st.write("No bookings saved yet. Create one under \"New booking\" to get started.")
    else:
        options = {
            f"#{p['id']} — {p['talent_name']} @ {p['venue_name'] or p['city']} ({p['city']}, {p['estimated_date']})": p["id"]
            for p in all_performances
        }
        default_label = next(
            (label for label, pid in options.items() if pid == st.session_state.get("selected_performance_id")),
            list(options.keys())[0],
        )
        chosen_label = st.selectbox("Select a booking to view", options=list(options.keys()),
                                     index=list(options.keys()).index(default_label))
        st.session_state["selected_performance_id"] = options[chosen_label]

st.divider()

# ---------------- dashboard for selected booking ----------------

performance_id = st.session_state.get("selected_performance_id")

if not performance_id:
    st.info("Save or select a booking above to see its dashboard.")
    st.stop()

performance_row = db.get_performance(performance_id)
if performance_row is None:
    st.warning("Selected booking no longer exists.")
    st.stop()

performance = dict(performance_row)
talent_row = db.get_talent(performance["talent_id"])
talent = enrichment.enrich_talent_if_needed(talent_row)
talent_name = talent["name"]
domain = talent["domain"]
talent_genres = json.loads(talent["genres_json"] or "[]")

st.header(f"{talent_name} — {performance['city']} ({performance['estimated_date']})")

revenue_info = metrics.estimate_revenue(performance, talent_name, domain)
if revenue_info["ticket_price_source"] == "default" and domain == "music" and config.HAS_TICKETMASTER:
    live_price = enrichment.ticketmaster.estimate_ticket_price_for_city(talent_name, performance["city"])
    if live_price:
        revenue_info["ticket_price"] = live_price
        revenue_info["ticket_price_source"] = "Ticketmaster live estimate"
        revenue_info["estimated_revenue"] = revenue_info["estimated_attendance"] * live_price

template = dict(db.get_default_expense_template())
expense_info = metrics.estimate_expenses(performance, template)
net_margin = metrics.estimate_net_margin(revenue_info, expense_info)

# ---------------- confidence scores ----------------

audience_row = db.get_audience_metrics(performance_id)
audience = dict(audience_row) if audience_row else None
financial_details_row = db.get_financial_details(performance_id)
financial_details = dict(financial_details_row) if financial_details_row else None
touring_history_row = db.get_touring_history(performance_id)
touring_history = dict(touring_history_row) if touring_history_row else None
market_competition_row = db.get_market_competition(performance_id)
market_competition = dict(market_competition_row) if market_competition_row else None

demand_score = metrics.score_demand(audience)
marketing_score = metrics.score_marketing(audience)
financial_score = metrics.score_financial(revenue_info, expense_info, performance, financial_details)
risk_score = metrics.score_risk(market_competition, touring_history)
overall_score = metrics.score_overall(demand_score, marketing_score, financial_score, risk_score)


def _render_score_card(col, label: str, result: dict, lower_is_better: bool = False):
    with col:
        score = result["score"]
        st.metric(label, f"{score:.0f}/100" if score is not None else "Not enough data",
                   help="Lower is better" if lower_is_better and score is not None else None)
        with st.popover("More"):
            if not result["breakdown"]:
                st.write("Not enough data entered yet - fill in the sections below.")
            else:
                st.dataframe(pd.DataFrame(result["breakdown"]), hide_index=True, width="stretch")


sc1, sc2, sc3, sc4, sc5 = st.columns(5)
_render_score_card(sc1, "Demand score", demand_score)
_render_score_card(sc2, "Financial score", financial_score)
_render_score_card(sc3, "Marketing score", marketing_score)
_render_score_card(sc4, "Risk score", risk_score, lower_is_better=True)
_render_score_card(sc5, "Overall viability", overall_score)

st.divider()

# ---------------- details (raw numbers behind the scores) ----------------

st.subheader("Details")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Estimated revenue", f"${revenue_info['estimated_revenue']:,.0f}",
          help=f"Ticket price: ${revenue_info['ticket_price']:.2f} ({revenue_info['ticket_price_source']})")
m2.metric("Estimated expenses", f"${expense_info['total_expenses']:,.0f}")
m3.metric("Estimated net margin", f"${net_margin:,.0f}")
m4.metric("Estimated attendance", f"{revenue_info['estimated_attendance']:,}",
          help=f"Sell-through: {revenue_info['sell_through_rate']:.0%} ({revenue_info['sell_through_rate_source']})")

st.caption(
    f"Revenue basis: {revenue_info['ticket_price_source']} ticket price, "
    f"{revenue_info['sell_through_rate_source']} sell-through rate."
)

if not expense_info["pct_sum_valid"]:
    st.warning(f"Expense template percentages sum to {expense_info['pct_sum']:.0%}, not 100%.")

# ---------------- expense breakdown ----------------

st.subheader("Expense breakdown")
breakdown_df = pd.DataFrame(
    [{"category": k, "amount": v} for k, v in expense_info["breakdown"].items()]
)
bc1, bc2 = st.columns([2, 3])
with bc1:
    st.dataframe(breakdown_df, hide_index=True, width="stretch")
with bc2:
    st.bar_chart(breakdown_df.set_index("category"))

with st.expander("Edit expense template percentages"):
    with st.form("expense_template_form"):
        venue_pct = st.slider("Venue %", 0.0, 1.0, template["venue_pct"])
        marketing_pct = st.slider("Marketing %", 0.0, 1.0, template["marketing_pct"])
        production_pct = st.slider("Production %", 0.0, 1.0, template["production_pct"])
        talent_fee_pct = st.slider("Talent fee %", 0.0, 1.0, template["talent_fee_pct"])
        other_pct = st.slider("Other %", 0.0, 1.0, template["other_pct"])
        if st.form_submit_button("Update template"):
            db.update_expense_template(
                template["id"], venue_pct, marketing_pct, production_pct, talent_fee_pct, other_pct
            )
            st.success("Template updated.")
            st.rerun()

# ---------------- demand & market intelligence ----------------

st.subheader("Demand & Market Intelligence")

demand_row = db.get_demand_metrics(performance_id)
demand = dict(demand_row) if demand_row else {}

dm1, dm2, dm3 = st.columns(3)
dm1.metric("Historical sell-through rate", f"{revenue_info['sell_through_rate']:.0%}",
           help=f"Source: {revenue_info['sell_through_rate_source']}")
vfs = metrics.venue_fit_score(revenue_info["estimated_attendance"], performance["target_capacity"])
dm2.metric("Venue fit score", f"{vfs:.0%}" if vfs is not None else "—",
           help="Predicted attendance ÷ venue capacity. Ideal range: 85-95%.")
mkt_efficiency = metrics.marketing_efficiency(
    expense_info["breakdown"]["marketing"], revenue_info["estimated_attendance"]
)
dm3.metric("Marketing efficiency", f"${mkt_efficiency:,.2f}/ticket" if mkt_efficiency is not None else "—",
           help="Marketing spend ÷ tickets sold. Lower is more efficient.")

with st.expander("Manually-entered demand metrics", expanded=not demand):
    st.caption(
        "These depend on data the agency tracks per artist/market (Google Trends, "
        "promoter history, etc.) rather than any connected API - fill in whichever you "
        "have; the rest stay blank. Audience/social platform stats have their own "
        "section below."
    )
    with st.form("demand_metrics_form"):
        col_a, col_b = st.columns(2)
        with col_a:
            search_interest_index = st.number_input(
                "Search interest / SEO score (Google Trends, last 90-180 days)", min_value=0.0,
                value=float(demand.get("search_interest_index") or 0.0), step=1.0)
            ticket_conversion_rate = st.number_input(
                "Ticket conversion rate (%) — buyers ÷ local followers/listeners", min_value=0.0,
                value=float(demand.get("ticket_conversion_rate") or 0.0), step=0.1)
            audience_purchasing_power = st.number_input(
                "Audience purchasing power (median household income, $)", min_value=0.0,
                value=float(demand.get("audience_purchasing_power") or 0.0), step=1000.0)
        with col_b:
            vip_conversion_rate = st.number_input(
                "VIP conversion rate (%) — VIP tickets ÷ total tickets", min_value=0.0,
                value=float(demand.get("vip_conversion_rate") or 0.0), step=0.1)
            promoter_reliability_score = st.number_input(
                "Promoter reliability score (0-100)", min_value=0.0, max_value=100.0,
                value=float(demand.get("promoter_reliability_score") or 0.0), step=1.0)
            fan_sentiment_score = st.number_input(
                "Fan sentiment score (0-100, sentiment across social platforms)", min_value=0.0, max_value=100.0,
                value=float(demand.get("fan_sentiment_score") or 0.0), step=1.0)

        if st.form_submit_button("Save demand metrics"):
            db.upsert_demand_metrics(
                performance_id,
                search_interest_index=search_interest_index or None,
                ticket_conversion_rate=ticket_conversion_rate or None,
                audience_purchasing_power=audience_purchasing_power or None,
                vip_conversion_rate=vip_conversion_rate or None,
                promoter_reliability_score=promoter_reliability_score or None,
                fan_sentiment_score=fan_sentiment_score or None,
            )
            st.success("Demand metrics saved.")
            st.rerun()

if demand:
    st.markdown("**Saved demand metrics**")
    labels = {
        "search_interest_index": "Search interest / SEO score",
        "ticket_conversion_rate": "Ticket conversion rate (%)",
        "audience_purchasing_power": "Audience purchasing power ($)",
        "vip_conversion_rate": "VIP conversion rate (%)",
        "promoter_reliability_score": "Promoter reliability score",
        "fan_sentiment_score": "Fan sentiment score",
    }
    display_rows = [
        {"Metric": label, "Value": demand[key]}
        for key, label in labels.items() if demand.get(key) is not None
    ]
    st.dataframe(pd.DataFrame(display_rows), hide_index=True, width="stretch")

# ---------------- audience & social media ----------------

st.subheader("Audience & Social Media")
st.caption("Feeds the Demand score (streaming/reach) and Marketing score (social platforms).")

with st.expander("Enter audience & social media data", expanded=not audience):
    with st.form("audience_metrics_form"):
        audience = audience or {}
        st.markdown("**Streaming / reach**")
        col_a, col_b = st.columns(2)
        with col_a:
            monthly_listeners = st.number_input("Monthly listeners", min_value=0.0,
                value=float(audience.get("monthly_listeners") or 0.0), step=1000.0)
            city_listeners = st.number_input("Listeners in this city", min_value=0.0,
                value=float(audience.get("city_listeners") or 0.0), step=100.0)
        with col_b:
            playlist_reach = st.number_input("Playlist reach", min_value=0.0,
                value=float(audience.get("playlist_reach") or 0.0), step=1000.0)
            growth_6mo_pct = st.number_input("Growth over last 6 months (%)",
                value=float(audience.get("growth_6mo_pct") or 0.0), step=0.5)

        st.markdown("**Instagram**")
        col_c, col_d = st.columns(2)
        with col_c:
            instagram_followers = st.number_input("Instagram followers", min_value=0.0,
                value=float(audience.get("instagram_followers") or 0.0), step=100.0)
            instagram_avg_likes = st.number_input("Average likes", min_value=0.0,
                value=float(audience.get("instagram_avg_likes") or 0.0), step=10.0, key="ig_likes")
        with col_d:
            instagram_avg_comments = st.number_input("Average comments", min_value=0.0,
                value=float(audience.get("instagram_avg_comments") or 0.0), step=1.0, key="ig_comments")
            instagram_engagement_pct = st.number_input("Engagement %", min_value=0.0,
                value=float(audience.get("instagram_engagement_pct") or 0.0), step=0.1, key="ig_engagement")

        st.markdown("**TikTok**")
        col_e, col_f = st.columns(2)
        with col_e:
            tiktok_followers = st.number_input("TikTok followers", min_value=0.0,
                value=float(audience.get("tiktok_followers") or 0.0), step=100.0)
            tiktok_avg_views = st.number_input("Average views", min_value=0.0,
                value=float(audience.get("tiktok_avg_views") or 0.0), step=100.0, key="tt_views")
        with col_f:
            tiktok_viral_rate_pct = st.number_input("Viral rate %", min_value=0.0,
                value=float(audience.get("tiktok_viral_rate_pct") or 0.0), step=0.1, key="tt_viral")

        st.markdown("**YouTube**")
        col_g, col_h = st.columns(2)
        with col_g:
            youtube_subscribers = st.number_input("Subscribers", min_value=0.0,
                value=float(audience.get("youtube_subscribers") or 0.0), step=100.0)
        with col_h:
            youtube_avg_views = st.number_input("Average views", min_value=0.0,
                value=float(audience.get("youtube_avg_views") or 0.0), step=100.0, key="yt_views")

        if st.form_submit_button("Save audience & social media data"):
            db.upsert_audience_metrics(
                performance_id,
                monthly_listeners=monthly_listeners or None, city_listeners=city_listeners or None,
                playlist_reach=playlist_reach or None, growth_6mo_pct=growth_6mo_pct or None,
                instagram_followers=instagram_followers or None, instagram_avg_likes=instagram_avg_likes or None,
                instagram_avg_comments=instagram_avg_comments or None,
                instagram_engagement_pct=instagram_engagement_pct or None,
                tiktok_followers=tiktok_followers or None, tiktok_avg_views=tiktok_avg_views or None,
                tiktok_viral_rate_pct=tiktok_viral_rate_pct or None,
                youtube_subscribers=youtube_subscribers or None, youtube_avg_views=youtube_avg_views or None,
            )
            st.success("Audience & social media data saved.")
            st.rerun()

# ---------------- detailed financial breakdown ----------------

st.subheader("Detailed financial breakdown")
st.caption(
    "Optional - the simple capacity/budget model above always works. Fill this in for a "
    "more precise Financial score based on real line items."
)

with st.expander("Enter detailed financial line items", expanded=False):
    with st.form("financial_details_form"):
        financial_details = financial_details or {}
        st.markdown("**Additional revenue**")
        col_a, col_b = st.columns(2)
        with col_a:
            vip_package_revenue = st.number_input("VIP package revenue ($)", min_value=0.0,
                value=float(financial_details.get("vip_package_revenue") or 0.0), step=100.0)
            merch_revenue = st.number_input("Merch revenue ($)", min_value=0.0,
                value=float(financial_details.get("merch_revenue") or 0.0), step=100.0)
            sponsorship_revenue = st.number_input("Sponsorship revenue ($)", min_value=0.0,
                value=float(financial_details.get("sponsorship_revenue") or 0.0), step=100.0)
        with col_b:
            food_pct = st.number_input("Food (% of ticket gross)", min_value=0.0,
                value=float(financial_details.get("food_pct") or 0.0), step=1.0)
            parking_pct = st.number_input("Parking (% of ticket gross)", min_value=0.0,
                value=float(financial_details.get("parking_pct") or 0.0), step=1.0)

        st.markdown("**Expenses**")
        col_c, col_d = st.columns(2)
        with col_c:
            artist_guarantee = st.number_input("Artist guarantee ($)", min_value=0.0,
                value=float(financial_details.get("artist_guarantee") or 0.0), step=100.0)
            venue_rental = st.number_input("Venue rental ($)", min_value=0.0,
                value=float(financial_details.get("venue_rental") or 0.0), step=100.0)
            production_cost = st.number_input("Production ($)", min_value=0.0,
                value=float(financial_details.get("production_cost") or 0.0), step=100.0)
            marketing_cost = st.number_input("Marketing ($)", min_value=0.0,
                value=float(financial_details.get("marketing_cost") or 0.0), step=100.0)
            security_cost = st.number_input("Security ($)", min_value=0.0,
                value=float(financial_details.get("security_cost") or 0.0), step=100.0)
        with col_d:
            insurance_cost = st.number_input("Insurance ($)", min_value=0.0,
                value=float(financial_details.get("insurance_cost") or 0.0), step=100.0)
            travel_cost = st.number_input("Travel ($)", min_value=0.0,
                value=float(financial_details.get("travel_cost") or 0.0), step=100.0)
            hotels_cost = st.number_input("Hotels ($)", min_value=0.0,
                value=float(financial_details.get("hotels_cost") or 0.0), step=100.0)
            crew_cost = st.number_input("Crew ($)", min_value=0.0,
                value=float(financial_details.get("crew_cost") or 0.0), step=100.0)
            taxes_cost = st.number_input("Taxes ($)", min_value=0.0,
                value=float(financial_details.get("taxes_cost") or 0.0), step=100.0)

        if st.form_submit_button("Save financial details"):
            db.upsert_financial_details(
                performance_id,
                vip_package_revenue=vip_package_revenue or None, merch_revenue=merch_revenue or None,
                sponsorship_revenue=sponsorship_revenue or None, food_pct=food_pct or None,
                parking_pct=parking_pct or None, artist_guarantee=artist_guarantee or None,
                venue_rental=venue_rental or None, production_cost=production_cost or None,
                marketing_cost=marketing_cost or None, security_cost=security_cost or None,
                insurance_cost=insurance_cost or None, travel_cost=travel_cost or None,
                hotels_cost=hotels_cost or None, crew_cost=crew_cost or None,
                taxes_cost=taxes_cost or None,
            )
            st.success("Financial details saved.")
            st.rerun()

# ---------------- touring history ----------------

st.subheader("Touring history")

with st.expander("Enter touring history", expanded=not touring_history):
    with st.form("touring_history_form"):
        touring_history = touring_history or {}
        progression_options = ["(not set)", "growing", "stable", "declining"]
        current_progression = touring_history.get("venue_size_progression") or "(not set)"

        sold_out_similar_venues = st.selectbox(
            "Has the artist sold out similar venues?", ["(not set)", "Yes", "No"],
            index=["(not set)", "Yes", "No"].index(
                "Yes" if touring_history.get("sold_out_similar_venues") is True
                else "No" if touring_history.get("sold_out_similar_venues") is False else "(not set)"
            ),
        )
        average_attendance_pct = st.number_input("Average attendance (% of capacity)", min_value=0.0,
            value=float(touring_history.get("average_attendance_pct") or 0.0), step=1.0)
        no_shows_count = st.number_input("No-shows (count)", min_value=0,
            value=int(touring_history.get("no_shows_count") or 0), step=1)
        average_ticket_price = st.number_input("Average ticket price ($)", min_value=0.0,
            value=float(touring_history.get("average_ticket_price") or 0.0), step=1.0)
        repeat_cities = st.selectbox(
            "Repeat cities? (has the artist played the same cities more than once)",
            ["(not set)", "Yes", "No"],
            index=["(not set)", "Yes", "No"].index(
                "Yes" if touring_history.get("repeat_cities") is True
                else "No" if touring_history.get("repeat_cities") is False else "(not set)"
            ),
        )
        festival_performance = st.selectbox(
            "Festival performance? (has the artist played festival slots)", ["(not set)", "Yes", "No"],
            index=["(not set)", "Yes", "No"].index(
                "Yes" if touring_history.get("festival_performance") is True
                else "No" if touring_history.get("festival_performance") is False else "(not set)"
            ),
        )
        venue_size_progression = st.selectbox(
            "Venue size progression", progression_options,
            index=progression_options.index(current_progression),
        )

        if st.form_submit_button("Save touring history"):
            def _tri_state(value):
                return None if value == "(not set)" else (value == "Yes")

            db.upsert_touring_history(
                performance_id,
                sold_out_similar_venues=_tri_state(sold_out_similar_venues),
                average_attendance_pct=average_attendance_pct or None,
                no_shows_count=int(no_shows_count),
                average_ticket_price=average_ticket_price or None,
                repeat_cities=_tri_state(repeat_cities),
                festival_performance=_tri_state(festival_performance),
                venue_size_progression=None if venue_size_progression == "(not set)" else venue_size_progression,
            )
            st.success("Touring history saved.")
            st.rerun()

# ---------------- market competition ----------------

st.subheader("Market competition")

with st.expander("Enter market competition data", expanded=not market_competition):
    with st.form("market_competition_form"):
        market_competition = market_competition or {}
        risk_options = ["(not set)", "low", "medium", "high"]
        current_risk = market_competition.get("weather_season_risk") or "(not set)"

        col_a, col_b = st.columns(2)
        with col_a:
            other_concerts_count = st.number_input("Other concerts nearby (count)", min_value=0,
                value=int(market_competition.get("other_concerts_count") or 0), step=1)
            sports_events_count = st.number_input("Sports events nearby (count)", min_value=0,
                value=int(market_competition.get("sports_events_count") or 0), step=1)
            festivals_count = st.number_input("Festivals nearby (count)", min_value=0,
                value=int(market_competition.get("festivals_count") or 0), step=1)
            local_events_count = st.number_input("Local events nearby (count)", min_value=0,
                value=int(market_competition.get("local_events_count") or 0), step=1)
        with col_b:
            major_holiday_conflict = st.checkbox("Major holiday conflict",
                value=bool(market_competition.get("major_holiday_conflict")))
            college_schedule_conflict = st.checkbox("College schedule conflict",
                value=bool(market_competition.get("college_schedule_conflict")))
            school_break_overlap = st.checkbox("School break overlap",
                value=bool(market_competition.get("school_break_overlap")))
            weather_season_risk = st.selectbox("Weather season risk", risk_options,
                index=risk_options.index(current_risk))

        if st.form_submit_button("Save market competition data"):
            db.upsert_market_competition(
                performance_id,
                other_concerts_count=int(other_concerts_count), sports_events_count=int(sports_events_count),
                festivals_count=int(festivals_count), local_events_count=int(local_events_count),
                major_holiday_conflict=major_holiday_conflict, college_schedule_conflict=college_schedule_conflict,
                school_break_overlap=school_break_overlap,
                weather_season_risk=None if weather_season_risk == "(not set)" else weather_season_risk,
            )
            st.success("Market competition data saved.")
            st.rerun()

# ---------------- comparison ----------------

st.subheader("Comparison with similar talent")
similar = metrics.rank_similar_talent(
    domain, exclude_name=talent_name, target_capacity=performance["target_capacity"],
    genres=talent_genres,
)
if not similar:
    st.write("No comparable talent data yet. Add historical comps below to enable comparisons.")
else:
    comp_df = pd.DataFrame(similar)
    comp_df = comp_df.rename(columns={
        "comparable_name": "Talent", "avg_capacity": "Avg capacity", "avg_attendance": "Avg attendance",
        "avg_ticket_price": "Avg ticket price", "avg_gross_revenue": "Avg gross revenue",
        "record_count": "# records", "genre_match": "Genre match", "capacity_delta": "Capacity delta",
    })
    st.dataframe(comp_df, hide_index=True, width="stretch")
    chart_df = pd.DataFrame(similar)[["comparable_name", "avg_gross_revenue"]].dropna()
    if not chart_df.empty:
        st.bar_chart(chart_df.set_index("comparable_name"))

# ---------------- historical performance ----------------

st.subheader("Historical performance")
summary = metrics.historical_summary(talent_name, domain, performance["city"])

hc1, hc2 = st.columns(2)
with hc1:
    st.markdown(f"**In {performance['city']}**")
    if summary["in_city"]:
        st.dataframe(pd.DataFrame(summary["in_city"]), hide_index=True, width="stretch")
    else:
        st.write("No records for this city yet.")
with hc2:
    st.markdown("**Elsewhere**")
    if summary["elsewhere"]:
        st.dataframe(pd.DataFrame(summary["elsewhere"]), hide_index=True, width="stretch")
    else:
        st.write("No records elsewhere yet.")

# ---------------- export ----------------

workbook_bytes = export.build_booking_workbook(
    talent=talent, performance=performance, revenue_info=revenue_info, expense_info=expense_info,
    net_margin=net_margin, venue_fit_score=vfs, marketing_efficiency=mkt_efficiency,
    demand=demand, audience=audience, financial_details=financial_details,
    touring_history=touring_history, market_competition=market_competition,
    scores={
        "demand": demand_score, "financial": financial_score, "marketing": marketing_score,
        "risk": risk_score, "overall": overall_score,
    },
    similar=similar, historical_summary=summary,
)
st.download_button(
    "Download booking report (.xlsx)",
    data=workbook_bytes,
    file_name=f"keymaster_{talent_name.replace(' ', '_')}_{performance['city'].replace(' ', '_')}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

# ---------------- live external data (supplementary, not used in the core math above) ----------------

with st.expander("Live external data"):
    live = enrichment.get_live_context(talent_name, domain, performance["city"])
    if domain == "music":
        if config.HAS_TICKETMASTER:
            price = live.get("ticketmaster_price_estimate")
            st.write(f"Ticketmaster price estimate: ${price:,.2f}" if price else "Ticketmaster: no matching events found.")
            events = live.get("ticketmaster_events") or []
            if events:
                st.dataframe(pd.DataFrame(events), hide_index=True, width="stretch")
        else:
            st.caption("Ticketmaster not configured — add TICKETMASTER_API_KEY to .env.")

        if config.HAS_SETLISTFM:
            history = live.get("setlistfm_history") or []
            if history:
                st.dataframe(pd.DataFrame(history), hide_index=True, width="stretch")
            else:
                st.write("Setlist.fm: no history found for this artist.")
        else:
            st.caption("Setlist.fm not configured — add SETLISTFM_API_KEY to .env.")
    else:
        if config.HAS_TMDB:
            profile = live.get("tmdb_profile")
            if profile:
                st.write(profile)
            else:
                st.write("TMDB: no matching person found.")
        else:
            st.caption("TMDB not configured — add TMDB_API_KEY to .env.")

    if talent_genres:
        st.caption(f"Detected genres: {', '.join(talent_genres)}")

# ---------------- manual data entry ----------------

with st.expander("Add historical comp record"):
    with st.form("historical_comp_form"):
        comp_is_self = st.checkbox(f"This record is about {talent_name} (uncheck for a comparable act)", value=True)
        comp_name = talent_name if comp_is_self else st.text_input("Comparable talent name")
        comp_venue = st.text_input("Venue", key="comp_venue")
        comp_city = st.text_input("City", value=performance["city"], key="comp_city")
        comp_date = st.date_input("Event date", key="comp_date")
        comp_capacity = st.number_input("Capacity", min_value=0, value=0, step=50, key="comp_capacity")
        comp_attendance = st.number_input("Attendance", min_value=0, value=0, step=50, key="comp_attendance")
        comp_price = st.number_input("Avg ticket price ($)", min_value=0.0, value=0.0, step=1.0, key="comp_price")
        comp_revenue = st.number_input("Gross revenue ($)", min_value=0.0, value=0.0, step=100.0, key="comp_revenue")
        comp_fee = st.number_input("Talent fee ($)", min_value=0.0, value=0.0, step=100.0, key="comp_fee")
        comp_expenses = st.number_input("Total expenses ($)", min_value=0.0, value=0.0, step=100.0, key="comp_expenses")

        if st.form_submit_button("Add record"):
            name_to_use = comp_name if comp_is_self else comp_name
            if not name_to_use:
                st.error("Comparable talent name is required.")
            else:
                db.create_historical_comp(
                    comparable_name=name_to_use,
                    domain=domain,
                    is_self=comp_is_self,
                    talent_id=talent["id"] if comp_is_self else None,
                    venue_name=comp_venue,
                    city=comp_city,
                    event_date=str(comp_date),
                    capacity=int(comp_capacity) or None,
                    attendance=int(comp_attendance) or None,
                    ticket_price_avg=comp_price or None,
                    gross_revenue=comp_revenue or None,
                    talent_fee=comp_fee or None,
                    total_expenses=comp_expenses or None,
                    source="manual",
                )
                st.success("Historical comp record added.")
                st.rerun()
