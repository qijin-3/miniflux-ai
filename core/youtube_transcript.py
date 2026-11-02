import re
import threading
import time
from urllib.parse import urlparse

import requests
from youtube_transcript_api import YouTubeTranscriptApi, RequestBlocked, IpBlocked
from youtube_transcript_api.proxies import GenericProxyConfig

from common.logger import logger

# Space consecutive YouTube requests globally: a burst (e.g. subscribing to a
# channel backfills dozens of videos) triggers YouTube IP rate-blocking.
# Observed threshold ~12-15 requests within a few minutes, so keep it slow.
MIN_REQUEST_INTERVAL_SECONDS = 25
# After a RequestBlocked/IpBlocked, back off progressively instead of probing
# every poll. Observed blocks are short-lived rate limits, so cap low:
# 10m, 20m, 30m ...
BLOCK_COOLDOWN_BASE_SECONDS = 600
BLOCK_COOLDOWN_MAX_SECONDS = 1800

_VIDEO_ID_RE = re.compile(
    r'(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})'
)

_api_cache = {}
_api_cache_lock = threading.Lock()
_block_state_lock = threading.Lock()
_block_cooldown_until = 0.0
_block_streak = 0
_last_request_time = 0.0


class TransientTranscriptError(Exception):
    """YouTube is rate-limiting/blocking us; retrying later may succeed."""


class _TimeoutSession(requests.Session):
    def __init__(self, timeout):
        super().__init__()
        self._timeout = timeout

    def request(self, *args, **kwargs):
        kwargs.setdefault('timeout', self._timeout)
        return super().request(*args, **kwargs)


def _build_http_client(config):
    session = _TimeoutSession(config.youtube_timeout)
    if config.youtube_cookie_path:
        from http.cookiejar import MozillaCookieJar
        try:
            cookie_jar = MozillaCookieJar(config.youtube_cookie_path)
            cookie_jar.load(ignore_discard=True, ignore_expires=True)
            session.cookies = cookie_jar
            logger.info(f"Loaded {len(cookie_jar)} YouTube cookies from {config.youtube_cookie_path}")
        except Exception as e:
            # missing/empty/invalid cookie file must not break entry processing
            logger.warning(f"Could not load YouTube cookies from {config.youtube_cookie_path}: {e}")
    return session


def _get_api(config):
    cache_key = (config.youtube_proxy, config.youtube_timeout, config.youtube_cookie_path)
    with _api_cache_lock:
        if cache_key not in _api_cache:
            proxy_config = None
            if config.youtube_proxy:
                proxy_config = GenericProxyConfig(https_url=config.youtube_proxy)
            _api_cache[cache_key] = YouTubeTranscriptApi(
                proxy_config=proxy_config,
                http_client=_build_http_client(config),
            )
        return _api_cache[cache_key]


def is_youtube_url(url):
    host = (urlparse(url or '').netloc or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    return host == 'youtube.com' or host == 'youtu.be' or host.endswith('.youtube.com')


def extract_video_id(url):
    match = _VIDEO_ID_RE.search(url or '')
    return match.group(1) if match else None


def _pace_requests():
    """Enforce a global minimum interval between YouTube requests."""
    global _last_request_time
    with _block_state_lock:
        wait = _last_request_time + MIN_REQUEST_INTERVAL_SECONDS - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_request_time = time.time()


def fetch_transcript(config, video_id):
    """Fetch the best transcript as (text, language, is_generated).

    Tries the configured languages in order; if none exists, falls back to
    any available transcript, preferring manually created over auto-generated.
    """
    api = _get_api(config)
    _pace_requests()
    try:
        transcript = api.fetch(video_id, languages=config.youtube_languages)
        return _transcript_text(transcript), transcript.language, transcript.is_generated
    except (RequestBlocked, IpBlocked):
        # transient: do not hammer YouTube further with a list() fallback
        raise
    except Exception:
        listed = api.list(video_id)
        candidates = sorted(listed, key=lambda t: t.is_generated)
        if not candidates:
            raise
        transcript = candidates[0].fetch()
        return _transcript_text(transcript), transcript.language, transcript.is_generated


def _transcript_text(transcript):
    return ' '.join(snippet.text.strip() for snippet in transcript if snippet.text)


def fetch_entry_transcript(config, entry):
    """Return the raw transcript text for a YouTube entry, or None.

    The transcript is LLM input only — it is never written into the Miniflux
    entry content. Raises TransientTranscriptError while YouTube is blocking
    us so the caller can defer the entry and retry on the next poll.
    """
    global _block_cooldown_until, _block_streak
    if not config.youtube_enabled:
        return None
    url = entry.get('url') or ''
    if not is_youtube_url(url):
        return None
    video_id = extract_video_id(url)
    if not video_id:
        return None

    with _block_state_lock:
        if time.time() < _block_cooldown_until:
            raise TransientTranscriptError(
                f"YouTube transcript fetching is in block cooldown for {url}"
            )

    try:
        text, language, generated = fetch_transcript(config, video_id)
    except (RequestBlocked, IpBlocked) as e:
        # temporary IP block: back off progressively and let the caller defer
        # this entry so the next poll retries instead of processing without it
        with _block_state_lock:
            cooldown = min(
                BLOCK_COOLDOWN_BASE_SECONDS * (2 ** _block_streak),
                BLOCK_COOLDOWN_MAX_SECONDS,
            )
            _block_streak += 1
            _block_cooldown_until = time.time() + cooldown
        raise TransientTranscriptError(f"YouTube blocked the request for {url}: {e}") from e
    except Exception as e:
        logger.warning(f"Could not fetch YouTube transcript for {url}: {e}")
        return None
    else:
        with _block_state_lock:
            _block_streak = 0

    if not text:
        return None
    if config.youtube_max_chars:
        text = text[: config.youtube_max_chars]
    source = language + ('（自动生成）' if generated else '')
    logger.info(f"Fetched YouTube transcript for {url} [{source}], {len(text)} chars")
    return text
