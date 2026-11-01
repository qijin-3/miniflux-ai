import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


def load_config_class():
    config_path = Path(__file__).resolve().parents[1] / "common" / "config.py"
    spec = importlib.util.spec_from_file_location("config_mod_category_llm", config_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Config


BASE_CONFIG = (
    "llm:\n"
    "  base_url: https://global.example/v1\n"
    "  api_key: global-key\n"
    "  model: global-model\n"
    "  timeout: 120\n"
    "  RPM: 30\n"
)


class CategoryLlmConfigTestCase(unittest.TestCase):
    def setUp(self):
        self.Config = load_config_class()
        self.old_cwd = os.getcwd()
        self.tmpdir = tempfile.TemporaryDirectory()
        os.chdir(self.tmpdir.name)

    def tearDown(self):
        os.chdir(self.old_cwd)
        self.tmpdir.cleanup()

    def write_config(self, content):
        Path("config.yml").write_text(content, encoding="utf8")

    def test_no_categories_returns_none(self):
        self.write_config(BASE_CONFIG)
        config = self.Config()
        self.assertIsNone(config.category_llm_config("Tech"))
        self.assertIsNone(config.category_llm_config("Missing"))

    def test_category_without_llm_returns_none(self):
        self.write_config(BASE_CONFIG + "categories:\n  Tech:\n    other: 1\n")
        config = self.Config()
        self.assertIsNone(config.category_llm_config("Tech"))

    def test_full_override(self):
        self.write_config(
            BASE_CONFIG
            + "categories:\n"
            + "  Tech:\n"
            + "    llm:\n"
            + "      base_url: https://other.example/v1\n"
            + "      api_key: other-key\n"
            + "      model: other-model\n"
        )
        config = self.Config()
        merged = config.category_llm_config("Tech")
        self.assertEqual(merged["base_url"], "https://other.example/v1")
        self.assertEqual(merged["api_key"], "other-key")
        self.assertEqual(merged["model"], "other-model")
        # unspecified keys fall back to the global llm section
        self.assertEqual(merged["timeout"], 120)
        self.assertEqual(merged["RPM"], 30)
        self.assertEqual(merged["provider"], "openai")
        self.assertEqual(merged["extra_params"], {})

    def test_other_categories_untouched(self):
        self.write_config(
            BASE_CONFIG
            + "categories:\n"
            + "  Tech:\n"
            + "    llm:\n"
            + "      model: other-model\n"
        )
        config = self.Config()
        self.assertIsNone(config.category_llm_config("Fun"))

    def test_extra_params_merged_over_global(self):
        self.write_config(
            BASE_CONFIG
            + "  extra_params:\n"
            + "    thinking_config:\n"
            + "      thinking_budget: 0\n"
            + "categories:\n"
            + "  Tech:\n"
            + "    llm:\n"
            + "      extra_params:\n"
            + "        temperature: 0.2\n"
        )
        config = self.Config()
        merged = config.category_llm_config("Tech")
        self.assertEqual(
            merged["extra_params"],
            {"thinking_config": {"thinking_budget": 0}, "temperature": 0.2},
        )


class LlmRoutesTestCase(unittest.TestCase):
    def setUp(self):
        self.Config = load_config_class()
        self.old_cwd = os.getcwd()
        self.tmpdir = tempfile.TemporaryDirectory()
        os.chdir(self.tmpdir.name)

    def tearDown(self):
        os.chdir(self.old_cwd)
        self.tmpdir.cleanup()

    def write_config(self, content):
        Path("config.yml").write_text(content, encoding="utf8")

    def test_no_routes_returns_none(self):
        self.write_config(BASE_CONFIG)
        config = self.Config()
        self.assertIsNone(config.route_llm_config("https://www.youtube.com/channel/UC123"))
        self.assertIsNone(config.route_llm_config(""))

    def test_pattern_match_merges_over_global(self):
        self.write_config(
            BASE_CONFIG
            + "llm_routes:\n"
            + "  'https://www.youtube.com/*':\n"
            + "    base_url: https://api.deepseek.com/v1\n"
            + "    api_key: ds-key\n"
            + "    model: deepseek-flash\n"
            + "    timeout: 120\n"
            + "    RPM: 60\n"
        )
        config = self.Config()
        merged = config.route_llm_config("https://www.youtube.com/channel/UC6t1O76G0jY")
        self.assertEqual(merged["model"], "deepseek-flash")
        self.assertEqual(merged["RPM"], 60)
        self.assertEqual(merged["timeout"], 120)
        # unspecified keys fall back to the global llm section
        self.assertEqual(merged["max_length"], None)

    def test_non_matching_url_returns_none(self):
        self.write_config(
            BASE_CONFIG
            + "llm_routes:\n"
            + "  'https://www.youtube.com/*':\n"
            + "    model: deepseek-flash\n"
        )
        config = self.Config()
        self.assertIsNone(config.route_llm_config("https://sspai.com/feed"))

    def test_empty_route_section_skipped(self):
        self.write_config(
            BASE_CONFIG
            + "llm_routes:\n"
            + "  'https://www.youtube.com/*':\n"
        )
        config = self.Config()
        self.assertIsNone(config.route_llm_config("https://www.youtube.com/channel/UC1"))


class AgentsFileTestCase(unittest.TestCase):
    def setUp(self):
        self.Config = load_config_class()
        self.old_cwd = os.getcwd()
        self.tmpdir = tempfile.TemporaryDirectory()
        os.chdir(self.tmpdir.name)

    def tearDown(self):
        os.chdir(self.old_cwd)
        self.tmpdir.cleanup()

    def write_config(self, content):
        Path("config.yml").write_text(content, encoding="utf8")

    def test_agents_file_overrides_inline(self):
        self.write_config(
            BASE_CONFIG
            + "agents_file: agents.yml\n"
            + "agents:\n"
            + "  inline_only:\n"
            + "    title: 't1:'\n"
            + "    prompt: p1\n"
            + "    style_block: false\n"
            + "  overridden:\n"
            + "    title: 'old:'\n"
            + "    prompt: old-prompt\n"
            + "    style_block: false\n"
        )
        Path("agents.yml").write_text(
            "overridden:\n"
            "  title: 'new:'\n"
            "  prompt: new-prompt\n"
            "  style_block: true\n"
            "  allow_list:\n"
            "    - https://www.youtube.com/*\n",
            encoding="utf8",
        )
        config = self.Config()
        self.assertEqual(
            sorted(config.agents.keys()), ["inline_only", "overridden"]
        )
        # file definition wins on conflicts
        self.assertEqual(config.agents["overridden"]["title"], "new:")
        self.assertEqual(config.agents["overridden"]["prompt"], "new-prompt")
        # inline-only agents are preserved
        self.assertEqual(config.agents["inline_only"]["title"], "t1:")

    def test_missing_agents_file_raises(self):
        self.write_config(BASE_CONFIG + "agents_file: nope.yml\nagents: {}\n")
        with self.assertRaises(FileNotFoundError):
            self.Config()

    def test_agents_directory_one_file_per_agent(self):
        self.write_config(
            BASE_CONFIG
            + "agents_file: agents.d\n"
            + "agents:\n"
            + "  inline_kept:\n"
            + "    title: 't1:'\n"
            + "    prompt: p1\n"
            + "    style_block: false\n"
        )
        Path("agents.d").mkdir()
        Path("agents.d/youtube_digest.yml").write_text(
            "title: '🎬 视频精读：'\n"
            "style_block: false\n"
            "prompt: |\n"
            "  long prompt here\n",
            encoding="utf8",
        )
        Path("agents.d/summary.yml").write_text(
            "title: '📝摘要：'\nstyle_block: true\nprompt: p2\n",
            encoding="utf8",
        )
        Path("agents.d/README.md").write_text("ignored", encoding="utf8")
        config = self.Config()
        # agent name comes from the filename stem; non-yaml files ignored
        self.assertEqual(
            sorted(config.agents.keys()), ["inline_kept", "summary", "youtube_digest"]
        )
        self.assertEqual(config.agents["youtube_digest"]["title"], "🎬 视频精读：")
        self.assertIn("long prompt", config.agents["youtube_digest"]["prompt"])
        self.assertEqual(config.agents["summary"]["title"], "📝摘要：")


if __name__ == "__main__":
    unittest.main()
