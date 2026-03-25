"""
Unit tests for douyin_scraper.py (no network access required).
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest

# Make sure the module under test is importable from the repo root
sys.path.insert(0, str(Path(__file__).parent))
import douyin_scraper as ds


# --------------------------------------------------------------------------- #
# _safe_filename
# --------------------------------------------------------------------------- #

class TestSafeFilename:
    def test_replaces_forbidden_chars(self):
        result = ds._safe_filename('video: "test" <clip>.mp4')
        assert ":" not in result
        assert '"' not in result
        assert "<" not in result
        assert ">" not in result

    def test_respects_max_len(self):
        long_name = "a" * 200
        result = ds._safe_filename(long_name, max_len=80)
        assert len(result) <= 80

    def test_normal_name_unchanged(self):
        result = ds._safe_filename("hello_world")
        assert result == "hello_world"


# --------------------------------------------------------------------------- #
# _extract_play_url
# --------------------------------------------------------------------------- #

class TestExtractPlayUrl:
    def _make_aweme(self, url_list):
        """Build a minimal aweme object with bit_rate list."""
        return {
            "bit_rate": [
                {
                    "play_addr": {
                        "url_list": url_list,
                    }
                }
            ]
        }

    def test_returns_first_http_url(self):
        aweme = self._make_aweme(["https://example.com/video.mp4"])
        assert ds._extract_play_url(aweme) == "https://example.com/video.mp4"

    def test_ignores_empty_url(self):
        aweme = self._make_aweme(["", "https://example.com/video.mp4"])
        assert ds._extract_play_url(aweme) == "https://example.com/video.mp4"

    def test_fallback_to_video_play_addr(self):
        aweme = {
            "video": {
                "play_addr": {
                    "url_list": ["https://cdn.example.com/v.mp4"]
                }
            }
        }
        assert ds._extract_play_url(aweme) == "https://cdn.example.com/v.mp4"

    def test_returns_none_when_no_url(self):
        assert ds._extract_play_url({}) is None


# --------------------------------------------------------------------------- #
# _parse_aweme_list
# --------------------------------------------------------------------------- #

class TestParseAwemeList:
    def _payload(self, aweme_list):
        return {"aweme_list": aweme_list}

    def test_empty_list(self):
        assert ds._parse_aweme_list({"aweme_list": []}) == []

    def test_parses_items(self):
        payload = self._payload([
            {
                "aweme_id": "123",
                "desc": "My video",
                "bit_rate": [
                    {"play_addr": {"url_list": ["https://example.com/1.mp4"]}}
                ],
            }
        ])
        items = ds._parse_aweme_list(payload)
        assert len(items) == 1
        assert items[0]["aweme_id"] == "123"
        assert items[0]["desc"] == "My video"
        assert items[0]["url"] == "https://example.com/1.mp4"

    def test_skips_items_without_url(self):
        payload = self._payload([
            {"aweme_id": "no-url", "desc": "no url video"}
        ])
        assert ds._parse_aweme_list(payload) == []

    def test_uses_aweme_id_as_desc_fallback(self):
        payload = self._payload([
            {
                "aweme_id": "999",
                "desc": "",
                "bit_rate": [
                    {"play_addr": {"url_list": ["https://example.com/x.mp4"]}}
                ],
            }
        ])
        items = ds._parse_aweme_list(payload)
        assert items[0]["desc"] == "999"


# --------------------------------------------------------------------------- #
# DouyinScraper – unit-level (no real browser)
# --------------------------------------------------------------------------- #

class TestDouyinScraperInit:
    def test_defaults(self):
        scraper = ds.DouyinScraper()
        assert scraper.output_dir == Path(ds.DEFAULT_OUTPUT_DIR)
        assert scraper.headless is True
        assert scraper.scroll_pause == ds.DEFAULT_SCROLL_PAUSE
        assert scraper.scroll_pause_jitter == ds.DEFAULT_SCROLL_PAUSE_JITTER
        assert scraper.max_scrolls == ds.DEFAULT_MAX_SCROLLS
        assert scraper.download_delay_min == ds.DEFAULT_DOWNLOAD_DELAY_MIN
        assert scraper.download_delay_max == ds.DEFAULT_DOWNLOAD_DELAY_MAX

    def test_custom_output_dir(self, tmp_path):
        scraper = ds.DouyinScraper(output_dir=str(tmp_path / "videos"))
        assert scraper.output_dir == tmp_path / "videos"

    def test_custom_jitter_and_delays(self):
        scraper = ds.DouyinScraper(
            scroll_pause_jitter=0.5,
            download_delay_min=2.0,
            download_delay_max=5.0,
        )
        assert scraper.scroll_pause_jitter == 0.5
        assert scraper.download_delay_min == 2.0
        assert scraper.download_delay_max == 5.0


class TestDownloadAll:
    def test_skips_existing_files(self, tmp_path, capsys):
        scraper = ds.DouyinScraper(output_dir=str(tmp_path))
        existing = tmp_path / "111_desc.mp4"
        existing.write_bytes(b"fake")

        videos = [{"aweme_id": "111", "desc": "desc", "url": "https://example.com/v.mp4"}]
        with patch.object(ds, "download_video", return_value=True) as mock_dl:
            scraper.download_all(videos)
            mock_dl.assert_not_called()

        captured = capsys.readouterr()
        assert "skipping" in captured.out

    def test_calls_download_video_for_new_files(self, tmp_path):
        scraper = ds.DouyinScraper(output_dir=str(tmp_path))
        videos = [{"aweme_id": "222", "desc": "new video", "url": "https://example.com/v.mp4"}]
        with patch.object(ds, "download_video", return_value=True) as mock_dl:
            scraper.download_all(videos)
            mock_dl.assert_called_once()
            call_args = mock_dl.call_args
            assert call_args[0][0] == "https://example.com/v.mp4"
            assert "222" in str(call_args[0][1])

    def test_passes_cookies_to_download_video(self, tmp_path):
        """Cookies loaded from file should be forwarded to download_video."""
        cookies_data = [{"name": "sessionid", "value": "abc123"}]
        cookies_file = tmp_path / "cookies.json"
        cookies_file.write_text(json.dumps(cookies_data))

        scraper = ds.DouyinScraper(
            output_dir=str(tmp_path),
            cookies_file=str(cookies_file),
        )
        videos = [{"aweme_id": "333", "desc": "v", "url": "https://example.com/v.mp4"}]
        with patch.object(ds, "download_video", return_value=True) as mock_dl:
            scraper.download_all(videos)
            mock_dl.assert_called_once()
            _, kwargs = mock_dl.call_args
            assert kwargs.get("cookies") == {"sessionid": "abc123"}

    def test_no_cookies_when_no_file(self, tmp_path):
        """When no cookies_file is set, cookies kwarg should be None."""
        scraper = ds.DouyinScraper(output_dir=str(tmp_path))
        videos = [{"aweme_id": "444", "desc": "v", "url": "https://example.com/v.mp4"}]
        with patch.object(ds, "download_video", return_value=True) as mock_dl:
            scraper.download_all(videos)
            _, kwargs = mock_dl.call_args
            assert kwargs.get("cookies") is None

    def test_random_delay_between_downloads(self, tmp_path):
        """A random delay should be inserted between successive downloads."""
        scraper = ds.DouyinScraper(
            output_dir=str(tmp_path),
            download_delay_min=0.5,
            download_delay_max=1.5,
        )
        videos = [
            {"aweme_id": "501", "desc": "a", "url": "https://example.com/a.mp4"},
            {"aweme_id": "502", "desc": "b", "url": "https://example.com/b.mp4"},
        ]
        sleep_calls = []
        with patch.object(ds, "download_video", return_value=True), \
             patch.object(ds.time, "sleep", side_effect=lambda t: sleep_calls.append(t)):
            scraper.download_all(videos)

        # Exactly one sleep call between two downloads
        assert len(sleep_calls) == 1
        assert 0.5 <= sleep_calls[0] <= 1.5

    def test_no_delay_after_last_download(self, tmp_path):
        """No delay should be added after the last video is downloaded."""
        scraper = ds.DouyinScraper(
            output_dir=str(tmp_path),
            download_delay_min=1.0,
            download_delay_max=2.0,
        )
        videos = [{"aweme_id": "601", "desc": "only", "url": "https://example.com/c.mp4"}]
        sleep_calls = []
        with patch.object(ds, "download_video", return_value=True), \
             patch.object(ds.time, "sleep", side_effect=lambda t: sleep_calls.append(t)):
            scraper.download_all(videos)

        assert sleep_calls == []


class TestOnResponse:
    def test_captures_post_api_responses(self):
        scraper = ds.DouyinScraper()

        payload = {
            "aweme_list": [
                {
                    "aweme_id": "abc",
                    "desc": "clip",
                    "bit_rate": [
                        {"play_addr": {"url_list": ["https://cdn.example.com/abc.mp4"]}}
                    ],
                }
            ]
        }

        mock_resp = MagicMock()
        mock_resp.url = "https://www.douyin.com/aweme/v1/web/aweme/post/?foo=bar"
        mock_resp.json.return_value = payload

        scraper._on_response(mock_resp)
        assert len(scraper._video_items) == 1
        assert scraper._video_items[0]["aweme_id"] == "abc"

    def test_ignores_unrelated_urls(self):
        scraper = ds.DouyinScraper()
        mock_resp = MagicMock()
        mock_resp.url = "https://example.com/some/other/endpoint"
        scraper._on_response(mock_resp)
        assert scraper._video_items == []

    def test_deduplicates_videos(self):
        scraper = ds.DouyinScraper()
        payload = {
            "aweme_list": [
                {
                    "aweme_id": "dup",
                    "desc": "duplicate",
                    "bit_rate": [
                        {"play_addr": {"url_list": ["https://cdn.example.com/dup.mp4"]}}
                    ],
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.url = "https://www.douyin.com/aweme/v1/web/aweme/post/"
        mock_resp.json.return_value = payload

        scraper._on_response(mock_resp)
        scraper._on_response(mock_resp)  # second call – same aweme_id
        assert len(scraper._video_items) == 1

    def test_tracks_has_more_true(self):
        """_on_response should set _has_more=True when API returns has_more=1."""
        scraper = ds.DouyinScraper()
        scraper._has_more = False  # start False; response should flip it True
        payload = {"aweme_list": [], "has_more": 1}
        mock_resp = MagicMock()
        mock_resp.url = "https://www.douyin.com/aweme/v1/web/aweme/post/"
        mock_resp.json.return_value = payload
        scraper._on_response(mock_resp)
        assert scraper._has_more is True

    def test_tracks_has_more_false(self):
        """_on_response should set _has_more=False when API returns has_more=0."""
        scraper = ds.DouyinScraper()
        scraper._has_more = True
        payload = {"aweme_list": [], "has_more": 0}
        mock_resp = MagicMock()
        mock_resp.url = "https://www.douyin.com/aweme/v1/web/aweme/post/"
        mock_resp.json.return_value = payload
        scraper._on_response(mock_resp)
        assert scraper._has_more is False

    def test_has_more_unchanged_when_field_absent(self):
        """_on_response should not change _has_more when has_more is absent."""
        scraper = ds.DouyinScraper()
        scraper._has_more = True
        payload = {"aweme_list": []}  # no has_more key
        mock_resp = MagicMock()
        mock_resp.url = "https://www.douyin.com/aweme/v1/web/aweme/post/"
        mock_resp.json.return_value = payload
        scraper._on_response(mock_resp)
        assert scraper._has_more is True  # unchanged


# --------------------------------------------------------------------------- #
# DouyinScraper._scroll_to_bottom (unit – no real browser)
# --------------------------------------------------------------------------- #

class TestScrollToBottom:
    def _make_page(self, item_counts: list[int]):
        """
        Return a mock Page whose evaluate() does nothing, and whose
        response handler will be called with incrementing video counts
        by directly setting scraper._video_items length.
        """
        return MagicMock()

    def test_stops_when_has_more_false_and_no_new_videos(self):
        """Scroll should stop after EARLY_STOP_NO_MORE fruitless scrolls when has_more is False."""
        scraper = ds.DouyinScraper(scroll_pause=0, scroll_pause_jitter=0)
        scraper._video_items = []  # start empty so prev_count=0 matches from the start
        scraper._consecutive_no_new = 0
        scraper._has_more = False  # API says no more content

        mock_page = MagicMock()
        scraper._scroll_to_bottom(mock_page)

        # Scroll 1 emits 1 evaluate (primary scrollBy; no fallback since
        # consecutive_no_new is still 0 at call time).
        # Scrolls 2..EARLY_STOP_NO_MORE each emit 2 evaluates (scrollBy + scrollIntoView).
        expected = 1 + 2 * (ds.EARLY_STOP_NO_MORE - 1)
        assert mock_page.evaluate.call_count == expected

    def test_continues_when_has_more_true(self):
        """When has_more is True, scroll should keep going past EARLY_STOP_NO_MORE
        and only stop at the safety-net threshold (SAFETY_STOP_CONSECUTIVE).
        max_scrolls is set higher than SAFETY_STOP_CONSECUTIVE so we hit the
        safety net before the scroll cap.
        """
        scraper = ds.DouyinScraper(scroll_pause=0, scroll_pause_jitter=0, max_scrolls=ds.SAFETY_STOP_CONSECUTIVE + 2)
        scraper._video_items = []  # start empty so prev_count=0 matches from the start
        scraper._consecutive_no_new = 0
        scraper._has_more = True  # API says there is more content

        mock_page = MagicMock()
        scraper._scroll_to_bottom(mock_page)

        # Scroll 1: 1 evaluate; scrolls 2..SAFETY_STOP_CONSECUTIVE: 2 evaluates each
        expected = 1 + 2 * (ds.SAFETY_STOP_CONSECUTIVE - 1)
        assert mock_page.evaluate.call_count == expected

    def test_uses_scroll_by_js(self):
        """Scroll should use scrollBy(0, innerHeight) for incremental scrolling."""
        scraper = ds.DouyinScraper(scroll_pause=0, scroll_pause_jitter=0, max_scrolls=1)
        scraper._video_items = []
        scraper._consecutive_no_new = 0
        scraper._has_more = False

        mock_page = MagicMock()
        scraper._scroll_to_bottom(mock_page)

        # First (and only) call is always the primary scrollBy call
        first_call_js = mock_page.evaluate.call_args_list[0][0][0]
        assert "scrollBy" in first_call_js
        assert "innerHeight" in first_call_js

    def test_scroll_pause_includes_jitter(self):
        """Each scroll step should sleep for scroll_pause + random jitter."""
        scraper = ds.DouyinScraper(
            scroll_pause=1.0,
            scroll_pause_jitter=2.0,
            max_scrolls=3,
        )
        scraper._video_items = []
        scraper._consecutive_no_new = 0
        scraper._has_more = False

        sleep_calls = []
        mock_page = MagicMock()
        with patch.object(ds.time, "sleep", side_effect=lambda t: sleep_calls.append(t)):
            scraper._scroll_to_bottom(mock_page)

        # Every sleep call should be in [scroll_pause, scroll_pause + jitter]
        assert len(sleep_calls) > 0
        for t in sleep_calls:
            assert 1.0 <= t <= 3.0  # scroll_pause=1.0, jitter max=2.0

# --------------------------------------------------------------------------- #
# _cookies_to_dict
# --------------------------------------------------------------------------- #

class TestCookiesToDict:
    def test_converts_list_to_dict(self):
        cookies = [
            {"name": "sessionid", "value": "abc"},
            {"name": "odin_tt", "value": "xyz"},
        ]
        result = ds._cookies_to_dict(cookies)
        assert result == {"sessionid": "abc", "odin_tt": "xyz"}

    def test_empty_list(self):
        assert ds._cookies_to_dict([]) == {}


# --------------------------------------------------------------------------- #
# DouyinScraper._get_cookies_dict
# --------------------------------------------------------------------------- #

class TestGetCookiesDict:
    def test_returns_none_when_no_file(self):
        scraper = ds.DouyinScraper()
        assert scraper._get_cookies_dict() is None

    def test_loads_and_converts_cookies(self, tmp_path):
        cookies_data = [{"name": "sessionid", "value": "tok123"}]
        cookies_file = tmp_path / "cookies.json"
        cookies_file.write_text(json.dumps(cookies_data))

        scraper = ds.DouyinScraper(cookies_file=str(cookies_file))
        result = scraper._get_cookies_dict()
        assert result == {"sessionid": "tok123"}

    def test_returns_none_on_missing_file(self, tmp_path, capsys):
        scraper = ds.DouyinScraper(cookies_file=str(tmp_path / "nonexistent.json"))
        result = scraper._get_cookies_dict()
        assert result is None
        assert "WARN" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# _cookies_indicate_login
# --------------------------------------------------------------------------- #

class TestCookiesIndicateLogin:
    def _c(self, name: str, value: str) -> dict:
        return {"name": name, "value": value}

    def test_sessionid_triggers_login(self):
        assert ds._cookies_indicate_login([self._c("sessionid", "abc123")]) is True

    def test_login_status_one_triggers_login(self):
        assert ds._cookies_indicate_login([self._c("LOGIN_STATUS", "1")]) is True

    def test_login_status_zero_does_not_trigger(self):
        assert ds._cookies_indicate_login([self._c("LOGIN_STATUS", "0")]) is False

    def test_empty_sessionid_does_not_trigger(self):
        assert ds._cookies_indicate_login([self._c("sessionid", "")]) is False

    def test_passport_csrf_token_does_not_trigger(self):
        """passport_csrf_token is set for all visitors and must not be a login signal."""
        assert ds._cookies_indicate_login([self._c("passport_csrf_token", "tok")]) is False

    def test_odin_tt_does_not_trigger(self):
        """odin_tt is set for all visitors and must not be a login signal."""
        assert ds._cookies_indicate_login([self._c("odin_tt", "tok")]) is False

    def test_empty_list_returns_false(self):
        assert ds._cookies_indicate_login([]) is False


# --------------------------------------------------------------------------- #
# fetch_cookies (unit – no real browser)
# --------------------------------------------------------------------------- #

class TestFetchCookies:
    def _make_cookie(self, name: str, value: str) -> dict:
        return {"name": name, "value": value, "domain": ".douyin.com", "path": "/"}

    def test_saves_cookies_to_file_on_login_detection(self, tmp_path):
        """fetch_cookies should write cookies.json and return the cookie list."""
        save_path = str(tmp_path / "out_cookies.json")
        fake_cookies = [self._make_cookie("sessionid", "secret")]

        mock_context = MagicMock()
        # First call to context.cookies() detects login (odin_tt present)
        mock_context.cookies.return_value = fake_cookies

        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context

        mock_page = MagicMock()
        mock_context.new_page.return_value = mock_page

        with patch("douyin_scraper.sync_playwright") as mock_pw_cm:
            mock_pw = MagicMock()
            mock_pw.__enter__ = MagicMock(return_value=mock_pw)
            mock_pw.__exit__ = MagicMock(return_value=False)
            mock_pw.chromium.launch.return_value = mock_browser
            mock_pw_cm.return_value = mock_pw

            result = ds.fetch_cookies(save_path=save_path, timeout=10)

        assert result == fake_cookies
        with open(save_path, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        assert saved == fake_cookies

    def test_saves_cookies_even_when_login_not_detected(self, tmp_path):
        """fetch_cookies should save cookies even if timeout elapses without login."""
        save_path = str(tmp_path / "out_cookies.json")
        # No login cookie names present
        fake_cookies = [self._make_cookie("anonymous", "anon")]

        mock_context = MagicMock()
        mock_context.cookies.return_value = fake_cookies

        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context

        mock_page = MagicMock()
        mock_context.new_page.return_value = mock_page

        with patch("douyin_scraper.sync_playwright") as mock_pw_cm, \
             patch("douyin_scraper.time") as mock_time:
            # Make time.time() exceed the deadline immediately after one iteration
            mock_time.time.side_effect = [0, 0, 999]  # 999 >> timeout=1, so deadline is exceeded
            mock_time.sleep = MagicMock()

            mock_pw = MagicMock()
            mock_pw.__enter__ = MagicMock(return_value=mock_pw)
            mock_pw.__exit__ = MagicMock(return_value=False)
            mock_pw.chromium.launch.return_value = mock_browser
            mock_pw_cm.return_value = mock_pw

            result = ds.fetch_cookies(save_path=save_path, timeout=1)

        assert result == fake_cookies
        assert Path(save_path).exists()

