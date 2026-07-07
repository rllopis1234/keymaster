import os

from dotenv import load_dotenv

load_dotenv()

TICKETMASTER_API_KEY = os.getenv("TICKETMASTER_API_KEY", "").strip()
SETLISTFM_API_KEY = os.getenv("SETLISTFM_API_KEY", "").strip()
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
APP_PASSWORD = os.getenv("APP_PASSWORD", "").strip()

HAS_TICKETMASTER = bool(TICKETMASTER_API_KEY)
HAS_SETLISTFM = bool(SETLISTFM_API_KEY)
HAS_SPOTIFY = bool(SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET)
HAS_TMDB = bool(TMDB_API_KEY)

API_STATUS = {
    "Ticketmaster": HAS_TICKETMASTER,
    "Setlist.fm": HAS_SETLISTFM,
    "Spotify": HAS_SPOTIFY,
    "TMDB": HAS_TMDB,
}
