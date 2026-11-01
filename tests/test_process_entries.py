import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

CONFIG_CONTENT = (
    "llm:\n"
    "  base_url: https://global.example/v1\n"
    "  api_key: global-key\n"
    "  model: global-model\n"
    "llm_routes:\n"
    "  'https://www.youtube.com/*':\n"
    "    model: route-model\n"
    "categories:\n"
    "  Tech:\n"
    "    llm:\n"
    "      model: category-model\n"
    "agents:\n"
    "  summary:\n"
    "    title: '📝摘要：'\n"
    "    prompt: 'summarize ${content}'\n"
    "    style_block: false\n"
    "    allow_list:\n"
    "      - https://www.youtube.com/*\n"
    "      - https://tech.example/*\n"
)


def make_entry(url="https://www.youtube.com/watch?v=AqqjH_uv-_w", content="<p>orig</p>", category="Tech"):
    return {
        "id": 42,
        "title": "Some video",
        "url": url,
        "content": content,
        "created_at": "2026-11-01T00:00:00Z",
        "feed": {"site_url": "https://www.youtube.com/channel/UCxyz", "category": {"title": category}},
    }


class ProcessEntryTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_cwd = os.getcwd()
        cls.tmpdir = tempfile.TemporaryDirectory()
        os.chdir(cls.tmpdir.name)
        Path("config.yml").write_text(CONFIG_CONTENT, encoding="utf8")
        Path("entries.json").unlink(missing_ok=True)
        # the package __init__ chain may import this module at collection time
        # with the repo-root config; reload so the module-level Config reads
        # the config written in this tmpdir
        import importlib
        import core.process_entries as module
        cls.module = importlib.reload(module)

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.old_cwd)
        cls.tmpdir.cleanup()

    def setUp(self):
        Path("entries.json").unlink(missing_ok=True)

    def test_transcript_is_llm_input_not_content(self):
        client = mock.Mock()
        with mock.patch.object(
            self.module, "fetch_entry_transcript", return_value="SUBTITLE TEXT"
        ) as fetch, mock.patch.object(
            self.module, "get_ai_result", return_value="AI 摘要内容"
        ) as ai:
            self.module.process_entry(client, make_entry())
        fetch.assert_called_once()
        # the agents saw the transcript-enriched content
        request = ai.call_args.args[1]
        self.assertIn("SUBTITLE TEXT", request)
        # the persisted content contains the agent block and the original
        # content, but NOT the transcript
        client.update_entry.assert_called_once()
        content = client.update_entry.call_args.kwargs["content"]
        self.assertIn("📝摘要：", content)
        self.assertIn("<p>orig</p>", content)
        self.assertNotIn("SUBTITLE TEXT", content)

    def test_transcript_not_fetched_when_no_agent_applies(self):
        client = mock.Mock()
        entry = make_entry()
        entry["feed"]["site_url"] = "https://not-allowed.example"
        with mock.patch.object(
            self.module, "fetch_entry_transcript"
        ) as fetch, mock.patch.object(self.module, "get_ai_result") as ai:
            self.module.process_entry(client, entry)
        fetch.assert_not_called()
        ai.assert_not_called()
        client.update_entry.assert_not_called()

    def test_already_processed_entry_skips_everything(self):
        client = mock.Mock()
        processed = "📝摘要：<p>old summary</p><p>orig</p>"
        entry = make_entry(content=processed)
        with mock.patch.object(
            self.module, "fetch_entry_transcript"
        ) as fetch, mock.patch.object(self.module, "get_ai_result") as ai:
            self.module.process_entry(client, entry)
        fetch.assert_not_called()
        ai.assert_not_called()
        client.update_entry.assert_not_called()

    def test_transient_block_defers_entry(self):
        client = mock.Mock()
        entry = make_entry()
        with mock.patch.object(
            self.module, "fetch_entry_transcript", side_effect=self.module.TransientTranscriptError("blocked")
        ), mock.patch.object(self.module, "get_ai_result") as ai:
            self.module.process_entry(client, entry)
        # entry untouched: no LLM call, no content update -> retried next poll
        ai.assert_not_called()
        client.update_entry.assert_not_called()

    def test_llm_resolution_route_category_global(self):
        client = mock.Mock()
        with mock.patch.object(self.module, "fetch_entry_transcript", return_value=None), \
             mock.patch.object(self.module, "get_ai_result", return_value="ok") as ai:
            # youtube site_url: llm_routes wins over the category override
            self.module.process_entry(client, make_entry())
            self.assertEqual(ai.call_args.kwargs["llm_config"]["model"], "route-model")

            # non-youtube feed in the Tech category: category override applies
            entry = make_entry()
            entry["feed"]["site_url"] = "https://tech.example/feed"
            self.module.process_entry(client, entry)
            self.assertEqual(ai.call_args.kwargs["llm_config"]["model"], "category-model")

            # non-youtube feed outside any override: agent filter skips it
            # (not in allow_list), so no llm config is ever resolved
            entry = make_entry()
            entry["feed"]["site_url"] = "https://other.example/feed"
            entry["feed"]["category"]["title"] = "Fun"
            calls_before = ai.call_count
            self.module.process_entry(client, entry)
            self.assertEqual(ai.call_count, calls_before)


if __name__ == "__main__":
    unittest.main()
