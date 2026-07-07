import hmac
from pathlib import Path

import pandas as pd
import streamlit as st
from st_keyup import st_keyup

import json

import config
import db
import enrichment
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
    st.dataframe(breakdown_df, hide_index=True, use_container_width=True)
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

merch_per_attendee = metrics.merch_spend_per_attendee(
    demand.get("merch_revenue_total"), revenue_info["estimated_attendance"]
)

with st.expander("Manually-entered demand metrics", expanded=not demand):
    st.caption(
        "These depend on data the agency tracks per artist/market (social platforms, "
        "Google Trends, promoter history, etc.) rather than any connected API - fill in "
        "whichever you have; the rest stay blank."
    )
    with st.form("demand_metrics_form"):
        col_a, col_b = st.columns(2)
        with col_a:
            local_fan_density = st.number_input(
                "Local fan density (followers per 100k residents)", min_value=0.0,
                value=float(demand.get("local_fan_density") or 0.0), step=1.0)
            search_interest_index = st.number_input(
                "Search interest / SEO score (Google Trends, last 90-180 days)", min_value=0.0,
                value=float(demand.get("search_interest_index") or 0.0), step=1.0)
            social_engagement_rate = st.number_input(
                "Social engagement rate (%) — (likes+comments+shares) ÷ followers", min_value=0.0,
                value=float(demand.get("social_engagement_rate") or 0.0), step=0.1)
            streaming_popularity = st.number_input(
                "Streaming popularity (monthly listeners in this city)", min_value=0.0,
                value=float(demand.get("streaming_popularity") or 0.0), step=100.0)
            ticket_conversion_rate = st.number_input(
                "Ticket conversion rate (%) — buyers ÷ local followers/listeners", min_value=0.0,
                value=float(demand.get("ticket_conversion_rate") or 0.0), step=0.1)
            audience_purchasing_power = st.number_input(
                "Audience purchasing power (median household income, $)", min_value=0.0,
                value=float(demand.get("audience_purchasing_power") or 0.0), step=1000.0)
        with col_b:
            market_competition_index = st.number_input(
                "Market competition index (# major events within ±14 days)", min_value=0,
                value=int(demand.get("market_competition_index") or 0), step=1)
            vip_conversion_rate = st.number_input(
                "VIP conversion rate (%) — VIP tickets ÷ total tickets", min_value=0.0,
                value=float(demand.get("vip_conversion_rate") or 0.0), step=0.1)
            merch_revenue_total = st.number_input(
                "Merchandise revenue ($, total for this show)", min_value=0.0,
                value=float(demand.get("merch_revenue_total") or 0.0), step=100.0)
            promoter_reliability_score = st.number_input(
                "Promoter reliability score (0-100)", min_value=0.0, max_value=100.0,
                value=float(demand.get("promoter_reliability_score") or 0.0), step=1.0)
            fan_sentiment_score = st.number_input(
                "Fan sentiment score (0-100, sentiment across social platforms)", min_value=0.0, max_value=100.0,
                value=float(demand.get("fan_sentiment_score") or 0.0), step=1.0)
            demand_growth_rate = st.number_input(
                "Demand growth rate (%) — 30-90 day momentum", min_value=-100.0,
                value=float(demand.get("demand_growth_rate") or 0.0), step=0.1)

        if st.form_submit_button("Save demand metrics"):
            db.upsert_demand_metrics(
                performance_id,
                local_fan_density=local_fan_density or None,
                search_interest_index=search_interest_index or None,
                social_engagement_rate=social_engagement_rate or None,
                streaming_popularity=streaming_popularity or None,
                ticket_conversion_rate=ticket_conversion_rate or None,
                audience_purchasing_power=audience_purchasing_power or None,
                market_competition_index=int(market_competition_index) or None,
                vip_conversion_rate=vip_conversion_rate or None,
                merch_revenue_total=merch_revenue_total or None,
                promoter_reliability_score=promoter_reliability_score or None,
                fan_sentiment_score=fan_sentiment_score or None,
                demand_growth_rate=demand_growth_rate or None,
            )
            st.success("Demand metrics saved.")
            st.rerun()

if demand:
    st.markdown("**Saved demand metrics**")
    labels = {
        "local_fan_density": "Local fan density (per 100k residents)",
        "search_interest_index": "Search interest / SEO score",
        "social_engagement_rate": "Social engagement rate (%)",
        "streaming_popularity": "Streaming popularity (monthly listeners)",
        "ticket_conversion_rate": "Ticket conversion rate (%)",
        "audience_purchasing_power": "Audience purchasing power ($)",
        "market_competition_index": "Market competition index (# events)",
        "vip_conversion_rate": "VIP conversion rate (%)",
        "merch_revenue_total": "Merchandise revenue ($)",
        "promoter_reliability_score": "Promoter reliability score",
        "fan_sentiment_score": "Fan sentiment score",
        "demand_growth_rate": "Demand growth rate (%)",
    }
    display_rows = [
        {"Metric": label, "Value": demand[key]}
        for key, label in labels.items() if demand.get(key) is not None
    ]
    if merch_per_attendee is not None:
        display_rows.append({"Metric": "Merchandise spend per attendee ($)", "Value": merch_per_attendee})
    st.dataframe(pd.DataFrame(display_rows), hide_index=True, use_container_width=True)

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
    st.dataframe(comp_df, hide_index=True, use_container_width=True)
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
        st.dataframe(pd.DataFrame(summary["in_city"]), hide_index=True, use_container_width=True)
    else:
        st.write("No records for this city yet.")
with hc2:
    st.markdown("**Elsewhere**")
    if summary["elsewhere"]:
        st.dataframe(pd.DataFrame(summary["elsewhere"]), hide_index=True, use_container_width=True)
    else:
        st.write("No records elsewhere yet.")

# ---------------- live external data (supplementary, not used in the core math above) ----------------

with st.expander("Live external data"):
    live = enrichment.get_live_context(talent_name, domain, performance["city"])
    if domain == "music":
        if config.HAS_TICKETMASTER:
            price = live.get("ticketmaster_price_estimate")
            st.write(f"Ticketmaster price estimate: ${price:,.2f}" if price else "Ticketmaster: no matching events found.")
            events = live.get("ticketmaster_events") or []
            if events:
                st.dataframe(pd.DataFrame(events), hide_index=True, use_container_width=True)
        else:
            st.caption("Ticketmaster not configured — add TICKETMASTER_API_KEY to .env.")

        if config.HAS_SETLISTFM:
            history = live.get("setlistfm_history") or []
            if history:
                st.dataframe(pd.DataFrame(history), hide_index=True, use_container_width=True)
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
