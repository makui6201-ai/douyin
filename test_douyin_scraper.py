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
    def test_scrolls_down_exactly_once(self):
        """_scroll_to_bottom should issue exactly one scrollBy call."""
        scraper = ds.DouyinScraper(scroll_pause=0, scroll_pause_jitter=0)
        scraper._video_items = []

        mock_page = MagicMock()
        with patch.object(ds.random, "uniform", return_value=2.0), \
             patch.object(ds.time, "sleep"):
            scraper._scroll_to_bottom(mock_page)

        assert mock_page.evaluate.call_count == 1
        js = mock_page.evaluate.call_args[0][0]
        assert "scrollBy(0, window.innerHeight)" in js

    def test_waits_between_1_and_5_seconds_before_scroll(self):
        """_scroll_to_bottom should sleep a random amount in [1, 5] before scrolling."""
        scraper = ds.DouyinScraper(scroll_pause=0, scroll_pause_jitter=0)
        scraper._video_items = []

        mock_page = MagicMock()
        sleep_calls = []
        with patch.object(ds.random, "uniform", return_value=3.5) as mock_uniform, \
             patch.object(ds.time, "sleep", side_effect=lambda t: sleep_calls.append(t)):
            scraper._scroll_to_bottom(mock_page)

        # random.uniform should have been called with (1, 5)
        mock_uniform.assert_any_call(1, 5)
        # The first sleep should be the pre-scroll delay (3.5 as returned by mock)
        assert sleep_calls[0] == 3.5

    def test_waits_scroll_pause_after_scroll(self):
        """_scroll_to_bottom should sleep scroll_pause + jitter seconds after scrolling."""
        scraper = ds.DouyinScraper(scroll_pause=2.5, scroll_pause_jitter=0)
        scraper._video_items = []

        mock_page = MagicMock()
        sleep_calls = []
        # First uniform call: pre-scroll delay (1, 5) → 1.0
        # Second uniform call: jitter (0, scroll_pause_jitter=0) → 0.0
        with patch.object(ds.random, "uniform", side_effect=[1.0, 0.0]), \
             patch.object(ds.time, "sleep", side_effect=lambda t: sleep_calls.append(t)):
            scraper._scroll_to_bottom(mock_page)

        # Two sleep calls: pre-scroll delay, then scroll_pause + jitter
        assert len(sleep_calls) == 2
        assert sleep_calls[1] == 2.5


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


# --------------------------------------------------------------------------- #
# DouyinScraper.fetch_video_list – scroll loop (unit – no real browser)
# --------------------------------------------------------------------------- #

class TestFetchVideoListScrollLoop:
    """Tests for the scroll loop inside fetch_video_list."""

    def _make_pw_mock(self):
        """Return a minimal playwright mock tree for fetch_video_list."""
        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_pw = MagicMock()
        mock_pw.__enter__ = MagicMock(return_value=mock_pw)
        mock_pw.__exit__ = MagicMock(return_value=False)
        mock_pw.chromium.launch.return_value = mock_browser
        return mock_pw, mock_page

    def test_scrolls_up_to_max_scrolls_when_has_more_stays_true(self):
        """fetch_video_list should scroll exactly max_scrolls times when has_more never becomes False."""
        scraper = ds.DouyinScraper(max_scrolls=4, scroll_pause=0, scroll_pause_jitter=0)
        mock_pw, _ = self._make_pw_mock()

        with patch("douyin_scraper.sync_playwright", return_value=mock_pw), \
             patch.object(scraper, "_scroll_to_bottom") as mock_scroll, \
             patch.object(ds.time, "sleep"):
            scraper.fetch_video_list("https://www.douyin.com/user/test")

        assert mock_scroll.call_count == 4

    def test_stops_early_when_has_more_becomes_false(self):
        """fetch_video_list should stop scrolling as soon as _has_more is False."""
        scraper = ds.DouyinScraper(max_scrolls=10, scroll_pause=0, scroll_pause_jitter=0)
        mock_pw, _ = self._make_pw_mock()

        call_count = 0

        def fake_scroll(page):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                scraper._has_more = False

        with patch("douyin_scraper.sync_playwright", return_value=mock_pw), \
             patch.object(scraper, "_scroll_to_bottom", side_effect=fake_scroll), \
             patch.object(ds.time, "sleep"):
            scraper.fetch_video_list("https://www.douyin.com/user/test")

        # Should have stopped after 2 scrolls (has_more set False at scroll 2)
        assert call_count == 2

    def test_has_more_reset_to_true_at_start(self):
        """fetch_video_list should reset _has_more=True at the start of each fetch."""
        scraper = ds.DouyinScraper(max_scrolls=3, scroll_pause=0, scroll_pause_jitter=0)
        mock_pw, _ = self._make_pw_mock()

        # Pre-set _has_more to False to confirm it gets reset
        scraper._has_more = False

        with patch("douyin_scraper.sync_playwright", return_value=mock_pw), \
             patch.object(scraper, "_scroll_to_bottom") as mock_scroll, \
             patch.object(ds.time, "sleep"):
            scraper.fetch_video_list("https://www.douyin.com/user/test")

        # Should have scrolled 3 times (not 0) – _has_more was reset to True
        assert mock_scroll.call_count == 3

