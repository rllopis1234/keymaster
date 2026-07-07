# Keymaster

A local booking-analytics tool for an entertainment agency. Enter a talent
(music artist/band or actor) and a planned booking — venue, city, date,
target capacity, budget — and get back estimated revenue, estimated
expenses, a comparison against similar talent, and the talent's historical
performance record in that city.

Runs via Streamlit, storing data in a Postgres database (works locally or
deployed to Streamlit Community Cloud). Works with zero API keys using
manually-entered data; gets progressively richer as you add free API keys.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in DATABASE_URL, API keys, APP_PASSWORD (see below)
streamlit run app.py
```

Opens at http://localhost:8501.

### Database (required)

The app needs a Postgres database — it no longer uses a local SQLite file.
The free tier of [Neon](https://neon.com) works well (scale-to-zero, no
credit card required): sign up, create a Project, copy the connection string
from the Connection Details panel, and set it as `DATABASE_URL` in `.env`:

```
DATABASE_URL=postgresql://user:password@host/dbname?sslmode=require
```

**Use a separate Neon project for development/tests than for a deployed
app** — running `pytest` truncates every table before each test, so pointing
it at production data would destroy it.

### Password protection (required before any public deployment)

Set `APP_PASSWORD` in `.env` to gate the whole app behind a single shared
password (compared with a constant-time check, stored as a secret, never in
git). **If `APP_PASSWORD` is left empty, the app has no login at all** — that's
fine for local-only use, but never deploy it publicly without setting one.

## Using it with no API keys

The app works fully offline on day one:
- Add a booking (talent, venue, city, date, capacity, budget).
- Add "historical comp" records manually (your own agency's past deals, or
  public numbers you already know) via the "Add historical comp record"
  expander on the dashboard.
- Revenue/expense estimates use those manual records, falling back to
  configurable global defaults (`GLOBAL_DEFAULT_TICKET_PRICE`,
  `GLOBAL_DEFAULT_SELL_THROUGH_RATE` in `metrics.py`) when nothing else is
  available.

This is the most important data source for **actors**: no public API
exposes appearance fees or box-office numbers for a specific actor, so
actor bookings will always lean on your own manually-entered comps for
anything financial. The APIs below only supply actor popularity/genre
metadata, not deal terms.

## Adding real API data (optional, free)

None of these are required to use the app. Add whichever you get around to;
each one only lights up its own section of the dashboard (status badges at
the top of the page show what's connected).

| Service | What it adds | Get a key | Notes |
|---|---|---|---|
| **MusicBrainz** | Genre tags for music artists | No key needed — works out of the box | Informal ~1 req/sec courtesy limit |
| **TMDB** | Actor popularity + genre (from filmography) | https://www.themoviedb.org/settings/api | Instant, free, generous limits |
| **Ticketmaster** | Live ticket price benchmarks, current event listings | https://developer.ticketmaster.com/ | Instant self-serve signup, free tier ~5000 req/day |
| **Setlist.fm** | Historical setlists/tour history by artist + city | https://api.setlist.fm/docs/1.0/index.html (apply via your account settings) | Approval may take longer than the others — not always instant |
| **Spotify** | Extra genre tags for music artists (best-effort) | https://developer.spotify.com/dashboard | Optional/lowest-priority source — Spotify has restricted several artist-data endpoints for new apps since late 2024, and some reports suggest further restrictions in 2026 (possibly requiring a Premium account for developer access). If it doesn't work for you, MusicBrainz already covers the same role and needs no key at all. |

Drop keys into `.env`:

```
TICKETMASTER_API_KEY=...
SETLISTFM_API_KEY=...
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
TMDB_API_KEY=...
```

Restart `streamlit run app.py` after editing `.env`.

## Deploying to Streamlit Community Cloud

1. Push this repo to GitHub (Community Cloud's free tier requires a connected
   GitHub repo).
2. On [share.streamlit.io](https://share.streamlit.io), connect the repo,
   set the main file to `app.py`.
3. In the app's **Settings → Secrets**, paste the same keys as your local
   `.env` (TOML format, e.g. `APP_PASSWORD = "..."`) — this includes
   `DATABASE_URL` and `APP_PASSWORD`, not just the API keys. Never commit
   `.env` itself.
4. In **App Settings**, you can choose the subdomain (e.g. `keymaster` for
   `keymaster.streamlit.app`). Note: Community Cloud's free tier only
   supports choosing this subdomain — it does not support pointing an
   externally-purchased domain at the app.

## How the numbers are computed

- **Estimated revenue** = target capacity × sell-through rate × ticket price.
  Ticket price and sell-through rate each resolve in priority order: your
  manual override on the booking → historical average for this talent in
  this city → historical average anywhere → global default. For music,
  if none of those exist and Ticketmaster is configured, a live price
  estimate is used instead of the global default.
- **Estimated expenses** = budget × the expense template percentages
  (venue/marketing/production/talent fee/other), editable in the dashboard.
- **Similar talent comparison** ranks other acts in your `historical_comps`
  data by shared genre tags (if available) and closeness of historical
  average venue capacity to this booking's target capacity.
- **Historical performance** shows this talent's own past records, split
  between the target city and elsewhere.

### Demand & Market Intelligence

A dedicated panel per booking covering the metrics agencies typically use to
size demand before committing to a show. Three are computed automatically
from data already on the booking:

- **Historical sell-through rate** — same value/source as the revenue estimate above.
- **Venue fit score** — estimated attendance ÷ target capacity (ideal range: 85-95%).
- **Marketing efficiency** — marketing spend ÷ estimated attendance ($/ticket).

The rest depend on data the agency tracks per artist/market (social
platforms, Google Trends, promoter history) that no connected API supplies,
so they're entered manually per booking via the "Manually-entered demand
metrics" form — fill in whichever you have, leave the rest blank:

local fan density, search interest index, social engagement rate, streaming
popularity, ticket conversion rate, audience purchasing power, market
competition index, VIP conversion rate, merchandise revenue (auto-converted
to spend-per-attendee), promoter reliability score, fan sentiment score, and
demand growth rate.

All of the above math is DB-driven and fully testable without any network
access — see `scripts/sanity_check.py` and `tests/`. Live API data (current
Ticketmaster listings, Setlist.fm history, TMDB profile) is shown separately
in the "Live external data" expander as supplementary context, and never
silently overrides your historical/manual numbers except for the one
documented Ticketmaster ticket-price fallback above.

## Project layout

```
app.py            Streamlit UI (single page) + password gate
db.py             Postgres schema + CRUD
models.py         Dataclasses mirroring the schema
metrics.py        Revenue/expense/similarity formulas (no network access)
enrichment.py     Bridges api_clients/* into db.py + supplies live context
config.py         .env/secrets loading, API-key + DB + password presence flags
api_clients/      One module per external API, each fails soft (returns
                  None/[] rather than raising) when unavailable
scripts/sanity_check.py   No-API-key smoke test
tests/            pytest suite for db.py/metrics.py
```

## Verifying it works

```bash
python scripts/sanity_check.py   # synthetic data, no API keys needed (uses DATABASE_URL)
pytest tests/                    # formula edge cases (requires DATABASE_URL - see above)
streamlit run app.py             # manual interactive check
```
