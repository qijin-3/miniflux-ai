import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

CONFIG_CONTENT = (
    "llm:\n"
    "  base_url: https://global.example/v1\n"
    "youtube:\n"
    "  enabled: true\n"
    "  languages: [zh-Hans, zh, en]\n"
)


def load_module():
    os.chdir(os.environ["YT_TEST_CWD"])
    import core.youtube_transcript as module
    return module


class YoutubeTranscriptTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_cwd = os.getcwd()
        cls.tmpdir = tempfile.TemporaryDirectory()
        os.chdir(cls.tmpdir.name)
        Path("config.yml").write_text(CONFIG_CONTENT, encoding="utf8")
        os.environ["YT_TEST_CWD"] = cls.tmpdir.name
        cls.module = load_module()

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.old_cwd)
        cls.tmpdir.cleanup()
        os.environ.pop("YT_TEST_CWD", None)

    def setUp(self):
        self.module._block_cooldown_until = 0.0
        self.module._block_streak = 0
        self.module._last_request_time = 0.0

    def make_config(self):
        return importlib.import_module("common.config").Config()

    def test_is_youtube_url(self):
        is_yt = self.module.is_youtube_url
        self.assertTrue(is_yt("https://www.youtube.com/watch?v=AqqjH_uv-_w"))
        self.assertTrue(is_yt("https://youtu.be/AqqjH_uv-_w"))
        self.assertTrue(is_yt("https://m.youtube.com/watch?v=AqqjH_uv-_w"))
        self.assertTrue(is_yt("https://music.youtube.com/watch?v=AqqjH_uv-_w"))
        self.assertFalse(is_yt("https://example.com/watch?v=AqqjH_uv-_w"))
        self.assertFalse(is_yt(""))
        self.assertFalse(is_yt(None))

    def test_extract_video_id(self):
        extract = self.module.extract_video_id
        self.assertEqual(extract("https://www.youtube.com/watch?v=AqqjH_uv-_w"), "AqqjH_uv-_w")
        self.assertEqual(extract("https://www.youtube.com/watch?v=AqqjH_uv-_w&t=30s"), "AqqjH_uv-_w")
        self.assertEqual(extract("https://youtu.be/AqqjH_uv-_w"), "AqqjH_uv-_w")
        self.assertEqual(extract("https://www.youtube.com/shorts/AqqjH_uv-_w"), "AqqjH_uv-_w")
        self.assertEqual(extract("https://www.youtube.com/embed/AqqjH_uv-_w"), "AqqjH_uv-_w")
        self.assertIsNone(extract("https://www.youtube.com/channel/UC6t1O76G0jYXOAoYCm153dA"))
        self.assertIsNone(extract("https://example.com/video"))

    def test_fetch_disabled(self):
        config = self.make_config()
        config.youtube_enabled = False
        entry = {"url": "https://www.youtube.com/watch?v=AqqjH_uv-_w", "content": "x"}
        self.assertIsNone(self.module.fetch_entry_transcript(config, entry))

    def test_fetch_non_youtube(self):
        config = self.make_config()
        entry = {"url": "https://example.com/post", "content": "x"}
        self.assertIsNone(self.module.fetch_entry_transcript(config, entry))

    def test_fetch_no_video_id(self):
        config = self.make_config()
        entry = {"url": "https://www.youtube.com/channel/UC6t1O76G0jYXOAoYCm153dA", "content": "x"}
        self.assertIsNone(self.module.fetch_entry_transcript(config, entry))

    def test_fetch_failure_returns_none(self):
        config = self.make_config()
        entry = {"url": "https://www.youtube.com/watch?v=AqqjH_uv-_w", "content": "x"}
        with mock.patch.object(
            self.module, "fetch_transcript", side_effect=RuntimeError("boom")
        ):
            self.assertIsNone(self.module.fetch_entry_transcript(config, entry))

    def test_ip_block_raises_transient(self):
        from youtube_transcript_api import RequestBlocked

        config = self.make_config()
        entry = {"url": "https://www.youtube.com/watch?v=AqqjH_uv-_w", "content": "x"}
        with mock.patch.object(
            self.module, "fetch_transcript", side_effect=RequestBlocked("AqqjH_uv-_w")
        ):
            with self.assertRaises(self.module.TransientTranscriptError):
                self.module.fetch_entry_transcript(config, entry)

    def test_block_triggers_cooldown_without_further_requests(self):
        from youtube_transcript_api import RequestBlocked

        config = self.make_config()
        entry = {"url": "https://www.youtube.com/watch?v=AqqjH_uv-_w", "content": "x"}
        with mock.patch.object(
            self.module, "fetch_transcript", side_effect=RequestBlocked("AqqjH_uv-_w")
        ) as fetch:
            with self.assertRaises(self.module.TransientTranscriptError):
                self.module.fetch_entry_transcript(config, entry)
            # still in cooldown: next call raises without touching YouTube
            with self.assertRaises(self.module.TransientTranscriptError):
                self.module.fetch_entry_transcript(config, entry)
        self.assertEqual(fetch.call_count, 1)

    def test_success_returns_raw_text(self):
        config = self.make_config()
        entry = {"url": "https://www.youtube.com/watch?v=AqqjH_uv-_w", "content": "x"}
        with mock.patch.object(
            self.module, "fetch_transcript", return_value=("Hello world. Second.", "English", True)
        ):
            text = self.module.fetch_entry_transcript(config, entry)
        self.assertEqual(text, "Hello world. Second.")

    def test_respects_max_chars(self):
        config = self.make_config()
        config.youtube_max_chars = 10
        entry = {"url": "https://www.youtube.com/watch?v=AqqjH_uv-_w", "content": "x"}
        with mock.patch.object(
            self.module, "fetch_transcript", return_value=("a" * 100, "English", False)
        ):
            self.assertEqual(self.module.fetch_entry_transcript(config, entry), "a" * 10)

    def test_cookies_loaded_into_http_client(self):
        config = self.make_config()
        cookie_file = Path("youtube_cookies.txt")
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n"
            "# https://curl.se/docs/http-cookies.html\n\n"
            ".youtube.com\tTRUE\t/\tTRUE\t0\tCONSENT\tYES+1\n",
            encoding="utf8",
        )
        config.youtube_cookie_path = str(cookie_file)
        client = self.module._build_http_client(config)
        self.assertTrue(any(c.name == "CONSENT" for c in client.cookies))

    def test_invalid_cookie_file_does_not_raise(self):
        config = self.make_config()
        cookie_file = Path("youtube_cookies.txt")
        cookie_file.write_text("placeholder", encoding="utf8")
        config.youtube_cookie_path = str(cookie_file)
        client = self.module._build_http_client(config)  # must not raise
        self.assertIsNone(client.cookies.get("CONSENT"))


if __name__ == "__main__":
    unittest.main()
