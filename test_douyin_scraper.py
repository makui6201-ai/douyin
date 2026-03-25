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
        assert scraper.max_scrolls == ds.DEFAULT_MAX_SCROLLS

    def test_custom_output_dir(self, tmp_path):
        scraper = ds.DouyinScraper(output_dir=str(tmp_path / "videos"))
        assert scraper.output_dir == tmp_path / "videos"


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
# fetch_cookies (unit – no real browser)
# --------------------------------------------------------------------------- #

class TestFetchCookies:
    def _make_cookie(self, name: str, value: str) -> dict:
        return {"name": name, "value": value, "domain": ".douyin.com", "path": "/"}

    def test_saves_cookies_to_file_on_login_detection(self, tmp_path):
        """fetch_cookies should write cookies.json and return the cookie list."""
        save_path = str(tmp_path / "out_cookies.json")
        fake_cookies = [self._make_cookie("odin_tt", "secret")]

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

