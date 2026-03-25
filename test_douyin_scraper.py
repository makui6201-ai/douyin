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
