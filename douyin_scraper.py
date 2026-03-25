"""
Douyin User Video Scraper
Scrapes a Douyin user's video list and downloads the videos locally.
"""

import json
import os
import random
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional, TypedDict
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright, Page, BrowserContext
from tqdm import tqdm

# Douyin login page URL used during cookie capture
DOUYIN_LOGIN_URL = "https://www.douyin.com/"

# Cookie names that indicate a successful Douyin login.
# NOTE: passport_csrf_token and odin_tt are set for *all* visitors (including
# unauthenticated ones) as soon as the page loads, so they must NOT be used as
# login indicators.  Only sessionid and LOGIN_STATUS (value "1") reliably
# confirm an authenticated session.
_LOGIN_COOKIE_NAMES = {"sessionid", "LOGIN_STATUS"}


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

DEFAULT_OUTPUT_DIR = "downloads"
DEFAULT_SCROLL_PAUSE = 3.0        # seconds between scroll steps
DEFAULT_SCROLL_PAUSE_JITTER = 2.0 # max additional random seconds added to each scroll pause
DEFAULT_MAX_SCROLLS = 200         # upper bound to avoid infinite loops (raised for large accounts)
DEFAULT_DOWNLOAD_DELAY_MIN = 1.0  # minimum random pause between downloads (seconds)
DEFAULT_DOWNLOAD_DELAY_MAX = 3.0  # maximum random pause between downloads (seconds)
# Consecutive fruitless scrolls needed to stop when API says no more content
EARLY_STOP_NO_MORE = 3
# Consecutive fruitless scrolls needed to stop regardless of has_more (safety net)
SAFETY_STOP_CONSECUTIVE = 10
class VideoItem(TypedDict):
    """A single video item returned by the scraper."""
    aweme_id: str
    desc: str
    url: str


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Regex that matches a valid Douyin video AWeme ID embedded in an API URL
AWEME_DETAIL_API_RE = re.compile(r"/aweme/v1/web/aweme/detail/", re.IGNORECASE)
AWEME_POST_API_RE = re.compile(r"/aweme/v1/web/aweme/post/", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Helper – safe filename
# --------------------------------------------------------------------------- #

def _safe_filename(name: str, max_len: int = 80) -> str:
    """Replace characters that are forbidden in file-system paths."""
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name[:max_len].strip()


def _cookies_to_dict(cookies: list) -> dict:
    """Convert a Playwright cookie list to a plain ``{name: value}`` dict
    suitable for use with :mod:`requests`."""
    return {c["name"]: c["value"] for c in cookies}


def _cookies_indicate_login(cookies: list) -> bool:
    """Return ``True`` when *cookies* contain evidence of a successful login.

    * ``sessionid`` – must be present and non-empty.
    * ``LOGIN_STATUS`` – must be present with value ``"1"``.

    Both ``odin_tt`` and ``passport_csrf_token`` are intentionally excluded:
    Douyin sets them for every visitor (authenticated or not) on the first
    page load, so they cannot be used as reliable login indicators.
    """
    for c in cookies:
        name = c.get("name", "")
        value = c.get("value", "")
        if name == "sessionid" and value:
            return True
        if name == "LOGIN_STATUS" and value == "1":
            return True
    return False


# --------------------------------------------------------------------------- #
# Auto cookie retrieval
# --------------------------------------------------------------------------- #

def fetch_cookies(
    save_path: str = "cookies.json",
    timeout: int = 120,
) -> list:
    """
    Open a **visible** browser window so the user can log in to Douyin
    manually.  Once a successful login is detected (or *timeout* seconds
    have elapsed), all browser cookies are extracted and written to
    *save_path* in JSON format.

    Parameters
    ----------
    save_path:
        File path where the cookies JSON will be written.
    timeout:
        Maximum number of seconds to wait for the user to complete login.

    Returns
    -------
    list
        The list of Playwright cookie dicts that were saved.
    """
    print(f"[INFO] Opening browser for manual login – please log in within {timeout}s.")
    print(f"[INFO] Cookies will be saved to: {save_path}")

    cookies: list = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent=DEFAULT_USER_AGENT,
            viewport={"width": 1280, "height": 900},
            locale="zh-CN",
        )
        page = context.new_page()
        page.goto(DOUYIN_LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)

        deadline = time.time() + timeout
        logged_in = False
        while time.time() < deadline:
            current_cookies = context.cookies()
            if _cookies_indicate_login(current_cookies):
                logged_in = True
                print("[INFO] Login detected.")
                break
            time.sleep(2)

        if not logged_in:
            print("[WARN] Login not detected within timeout; saving cookies anyway.")

        cookies = context.cookies()
        browser.close()

    dest = Path(save_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(cookies, fh, ensure_ascii=False, indent=2)

    print(f"[INFO] Saved {len(cookies)} cookie(s) to {save_path}")
    return cookies


# --------------------------------------------------------------------------- #
# Video download
# --------------------------------------------------------------------------- #

def download_video(
    url: str,
    dest_path: Path,
    cookies: Optional[dict] = None,
    chunk_size: int = 1 << 16,
) -> bool:
    """
    Download a single video from *url* and write it to *dest_path*.

    Parameters
    ----------
    url:
        Direct MP4 URL to download.
    dest_path:
        Local file path to write the video to.
    cookies:
        Optional ``{name: value}`` cookie dict forwarded to :mod:`requests`
        (used when the video URL requires an authenticated session).

    Returns True on success, False otherwise.
    """
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Referer": "https://www.douyin.com/",
        "Range": "bytes=0-",
    }
    try:
        with requests.get(
            url,
            headers=headers,
            cookies=cookies or {},
            stream=True,
            timeout=60,
        ) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(dest_path, "wb") as fh, tqdm(
                desc=dest_path.name,
                total=total,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                leave=False,
            ) as bar:
                for chunk in resp.iter_content(chunk_size=chunk_size):
                    fh.write(chunk)
                    bar.update(len(chunk))
        return True
    except Exception as exc:
        print(f"  [ERROR] Failed to download {dest_path.name}: {exc}")
        if dest_path.exists():
            dest_path.unlink()
        return False


# --------------------------------------------------------------------------- #
# Video info extraction from API response payloads
# --------------------------------------------------------------------------- #

def _extract_play_url(video_obj: dict) -> Optional[str]:
    """
    Try multiple paths inside a Douyin *video_obj* dict to find a usable
    MP4 play URL (prefer no-watermark variants).
    """
    bit_rate_list = (
        video_obj.get("bit_rate") or
        video_obj.get("video", {}).get("bit_rate") or
        []
    )
    # Try to find a no-watermark URL in bit_rate items
    for item in bit_rate_list:
        play = item.get("play_addr", {})
        urls = play.get("url_list") or []
        for u in urls:
            if u and u.startswith("http"):
                return u

    # Fallback: direct play_addr on the video sub-object
    video_sub = video_obj.get("video", {})
    for key in ("play_addr_h264", "play_addr", "download_addr"):
        play = video_sub.get(key, {})
        urls = play.get("url_list") or []
        for u in urls:
            if u and u.startswith("http"):
                return u

    return None


def _parse_aweme_list(payload: dict) -> list[VideoItem]:
    """
    Extract a list of ``{"aweme_id": ..., "desc": ..., "url": ...}`` dicts
    from a raw Douyin API response payload.
    """
    items = []
    aweme_list = payload.get("aweme_list") or []
    for aweme in aweme_list:
        aweme_id = aweme.get("aweme_id", "unknown")
        desc = aweme.get("desc") or aweme_id
        url = _extract_play_url(aweme)
        if url:
            items.append({"aweme_id": aweme_id, "desc": desc, "url": url})
    return items


# --------------------------------------------------------------------------- #
# Browser-based scraper
# --------------------------------------------------------------------------- #

class DouyinScraper:
    """
    Automates a headless Chromium browser to visit a Douyin user page,
    scroll through all visible videos, intercept the API responses that
    carry video metadata, and return a list of downloadable video items.
    """

    def __init__(
        self,
        output_dir: str = DEFAULT_OUTPUT_DIR,
        headless: bool = True,
        scroll_pause: float = DEFAULT_SCROLL_PAUSE,
        scroll_pause_jitter: float = DEFAULT_SCROLL_PAUSE_JITTER,
        max_scrolls: int = DEFAULT_MAX_SCROLLS,
        cookies_file: Optional[str] = None,
        download_delay_min: float = DEFAULT_DOWNLOAD_DELAY_MIN,
        download_delay_max: float = DEFAULT_DOWNLOAD_DELAY_MAX,
    ):
        self.output_dir = Path(output_dir)
        self.headless = headless
        self.scroll_pause = scroll_pause
        self.scroll_pause_jitter = scroll_pause_jitter
        self.max_scrolls = max_scrolls
        self.cookies_file = cookies_file
        self.download_delay_min = download_delay_min
        self.download_delay_max = download_delay_max
        self._video_items: list[VideoItem] = []

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _on_response(self, response) -> None:
        """Playwright response handler – captures Douyin video API calls."""
        url = response.url
        if not (AWEME_POST_API_RE.search(url) or AWEME_DETAIL_API_RE.search(url)):
            return
        try:
            body = response.json()
            items = _parse_aweme_list(body)
            new = [i for i in items if i["aweme_id"] not in
                   {v["aweme_id"] for v in self._video_items}]
            if new:
                print(f"  [API] Captured {len(new)} new video(s) from: {url.split('?')[0]}")
                self._video_items.extend(new)
            # Track pagination status so the scroll loop knows when to stop
            has_more = body.get("has_more")
            if has_more is not None:
                self._has_more = bool(has_more)
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            print(f"  [WARN] Could not parse API response from {url.split('?')[0]}: {exc}")

    def _load_cookies(self, context: BrowserContext) -> None:
        if not self.cookies_file:
            return
        with open(self.cookies_file, "r", encoding="utf-8") as fh:
            cookies = json.load(fh)
        context.add_cookies(cookies)
        print(f"  [INFO] Loaded {len(cookies)} cookies from {self.cookies_file}")

    def _get_cookies_dict(self) -> Optional[dict]:
        """Return cookies from *cookies_file* as a ``{name: value}`` dict,
        or ``None`` when no cookies file is configured."""
        if not self.cookies_file:
            return None
        try:
            with open(self.cookies_file, "r", encoding="utf-8") as fh:
                cookies = json.load(fh)
            return _cookies_to_dict(cookies)
        except Exception as exc:
            print(f"  [WARN] Could not read cookies from {self.cookies_file}: {exc}")
            return None

    def _scroll_to_bottom(self, page: Page) -> None:
        """
        Scroll down the page repeatedly until no new content loads or
        *max_scrolls* is reached.

        Uses incremental scrolling (one viewport height at a time) so that
        Douyin's IntersectionObserver-based load-more sentinel actually passes
        through the viewport and triggers the next API call.

        When stuck, progressive recovery strategies are applied:
        - Level 1 (any consecutive miss): scroll to the absolute page bottom so
          the lazy-load sentinel is guaranteed to enter the viewport.
        - Level 2 (3+ consecutive misses): scroll back up two viewport heights
          and then return to the bottom, forcing the IntersectionObserver to
          fire again as the sentinel re-enters the viewport.

        A random jitter is added to every pause to simulate human scrolling
        speed and reduce the chance of anti-bot detection.
        """
        prev_count = 0
        for step in range(self.max_scrolls):
            # Primary scroll: advance one viewport height
            page.evaluate("window.scrollBy(0, window.innerHeight)")

            # Level-1 recovery: scroll to absolute page bottom so the sentinel
            # is guaranteed to enter the viewport
            if self._consecutive_no_new > 0:
                page.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight"
                    " || document.documentElement.scrollHeight);"
                )

            # Level-2 recovery: scroll up and back down to re-trigger the
            # IntersectionObserver on the load-more sentinel
            if self._consecutive_no_new >= 3:
                page.evaluate("window.scrollBy(0, -window.innerHeight * 2);")
                page.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight"
                    " || document.documentElement.scrollHeight);"
                )

            time.sleep(self.scroll_pause + random.uniform(0, self.scroll_pause_jitter))
            cur_count = len(self._video_items)
            print(
                f"  [SCROLL {step + 1}/{self.max_scrolls}] "
                f"Videos captured so far: {cur_count}"
            )
            if cur_count == prev_count:
                self._consecutive_no_new += 1
                # Stop only when the API confirms no more content, or after
                # many consecutive fruitless scrolls (safety net)
                if self._consecutive_no_new >= EARLY_STOP_NO_MORE and not self._has_more:
                    print("  [INFO] No new videos and API reports no more content. Stopping scroll.")
                    break
                if self._consecutive_no_new >= SAFETY_STOP_CONSECUTIVE:
                    print("  [INFO] No new videos detected after multiple scrolls. Stopping scroll.")
                    break
            else:
                self._consecutive_no_new = 0
            prev_count = cur_count

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def fetch_video_list(self, user_url: str) -> list[VideoItem]:
        """
        Navigate to *user_url* (a Douyin user profile page), scroll through
        the video list, and return a list of :class:`VideoItem` dicts:

            [{"aweme_id": "...", "desc": "...", "url": "..."}, ...]
        """
        self._video_items = []
        self._consecutive_no_new = 0
        self._has_more = True  # assume more content until API says otherwise

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            context = browser.new_context(
                user_agent=DEFAULT_USER_AGENT,
                viewport={"width": 1280, "height": 900},
                locale="zh-CN",
            )
            self._load_cookies(context)
            page = context.new_page()
            page.on("response", self._on_response)

            print(f"[INFO] Navigating to: {user_url}")
            page.goto(user_url, wait_until="domcontentloaded", timeout=60_000)

            # Wait for the video grid to appear
            try:
                page.wait_for_selector(
                    'div[data-e2e="user-post-list"],'
                    'ul[data-e2e="user-post-list"],'
                    'div[class*="video-list"]',
                    timeout=15_000,
                )
            except Exception:
                print("  [WARN] Video list container not found; will still try to scroll.")

            time.sleep(3)  # let initial API responses settle
            self._scroll_to_bottom(page)

            browser.close()

        print(f"[INFO] Total videos found: {len(self._video_items)}")
        return list(self._video_items)

    def download_all(self, video_items: list[VideoItem]) -> None:
        """Download every video in *video_items* to ``self.output_dir``."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        cookies = self._get_cookies_dict()

        total = len(video_items)
        for idx, item in enumerate(video_items, start=1):
            aweme_id = item["aweme_id"]
            desc = _safe_filename(item.get("desc", aweme_id))
            filename = f"{aweme_id}_{desc}.mp4"
            dest = self.output_dir / filename

            if dest.exists():
                print(f"[{idx}/{total}] Already exists, skipping: {filename}")
                continue

            print(f"[{idx}/{total}] Downloading: {filename}")
            download_video(item["url"], dest, cookies=cookies)
            if idx < total:
                delay = random.uniform(self.download_delay_min, self.download_delay_max)
                time.sleep(delay)

    def run(self, user_url: str) -> None:
        """Convenience method: fetch video list then download everything."""
        videos = self.fetch_video_list(user_url)
        if not videos:
            print("[WARN] No downloadable videos found.")
            return
        self.download_all(videos)


# --------------------------------------------------------------------------- #
# CLI entry point
# --------------------------------------------------------------------------- #

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Download all public videos from a Douyin user profile.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "url",
        nargs="?",
        default=(
            "https://www.douyin.com/user/"
            "MS4wLjABAAAAl61SDq2w6mLhMWpv1-ABXqdBRV9nrcyr140Oxf3aPiXE_L0bt5XR15XGm2SajP72"
        ),
        help="Douyin user profile URL",
    )
    parser.add_argument(
        "-o", "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to save downloaded videos",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Show browser window (useful for debugging or manual login)",
    )
    parser.add_argument(
        "--scroll-pause",
        type=float,
        default=DEFAULT_SCROLL_PAUSE,
        help="Seconds to pause between scroll steps",
    )
    parser.add_argument(
        "--scroll-jitter",
        type=float,
        default=DEFAULT_SCROLL_PAUSE_JITTER,
        help="Max additional random seconds added to each scroll pause (prevents rate limiting)",
    )
    parser.add_argument(
        "--max-scrolls",
        type=int,
        default=DEFAULT_MAX_SCROLLS,
        help="Maximum number of scroll steps",
    )
    parser.add_argument(
        "--download-delay-min",
        type=float,
        default=DEFAULT_DOWNLOAD_DELAY_MIN,
        help="Minimum random delay in seconds between video downloads",
    )
    parser.add_argument(
        "--download-delay-max",
        type=float,
        default=DEFAULT_DOWNLOAD_DELAY_MAX,
        help="Maximum random delay in seconds between video downloads",
    )
    parser.add_argument(
        "--cookies",
        default=None,
        metavar="FILE",
        help=(
            "Path to a JSON file containing browser cookies "
            "(useful when the page requires login)"
        ),
    )
    parser.add_argument(
        "--save-cookies",
        default=None,
        metavar="FILE",
        help=(
            "Open a browser window so you can log in to Douyin manually, "
            "then save the captured cookies to FILE (default: cookies.json). "
            "The program exits after saving; use --cookies FILE on subsequent runs."
        ),
    )
    parser.add_argument(
        "--login-timeout",
        type=int,
        default=120,
        metavar="SECONDS",
        help="Seconds to wait for manual login when --save-cookies is used",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Only print the video list; do not download",
    )

    args = parser.parse_args()

    # ------------------------------------------------------------------ #
    # --save-cookies: capture cookies interactively, then exit
    # ------------------------------------------------------------------ #
    if args.save_cookies is not None:
        save_path = args.save_cookies or "cookies.json"
        fetch_cookies(save_path=save_path, timeout=args.login_timeout)
        print(f"[INFO] Done. Run with --cookies {save_path} to use these cookies.")
        return

    scraper = DouyinScraper(
        output_dir=args.output_dir,
        headless=not args.no_headless,
        scroll_pause=args.scroll_pause,
        scroll_pause_jitter=args.scroll_jitter,
        max_scrolls=args.max_scrolls,
        cookies_file=args.cookies,
        download_delay_min=args.download_delay_min,
        download_delay_max=args.download_delay_max,
    )

    if args.list_only:
        videos = scraper.fetch_video_list(args.url)
        for v in videos:
            print(f"{v['aweme_id']}  {v['desc']}")
            print(f"  {v['url']}")
    else:
        scraper.run(args.url)


if __name__ == "__main__":
    main()
