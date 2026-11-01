from fnmatch import fnmatch
from pathlib import Path
from yaml import safe_load

# LLM settings that a category-level `categories.<title>.llm` section may override;
# any key not overridden falls back to the global `llm` section.
LLM_CONFIG_KEYS = (
    'provider', 'base_url', 'api_key', 'model',
    'max_length', 'timeout', 'RPM', 'extra_params',
)

class Config:
    def __init__(self):
        with open('config.yml', encoding='utf8') as config_file:
            self.c = safe_load(config_file)
        self.log_level = self.c.get('log_level', 'INFO')

        self.miniflux_base_url = self.get_config_value('miniflux', 'base_url', None)
        self.miniflux_api_key = self.get_config_value('miniflux', 'api_key', None)
        self.miniflux_webhook_secret = self.get_config_value('miniflux', 'webhook_secret', None)
        self.miniflux_schedule_interval = self.get_config_value('miniflux', 'schedule_interval', None)

        self.llm_provider = self.get_config_value('llm', 'provider', 'openai')
        self.llm_base_url = self.get_config_value('llm', 'base_url', None)
        self.llm_api_key = self.get_config_value('llm', 'api_key', None)
        self.llm_model = self.get_config_value('llm', 'model', None)
        self.llm_max_length = self.get_config_value('llm', 'max_length', None)
        self.llm_timeout = self.get_config_value('llm', 'timeout', 60)
        self.llm_max_workers = self.get_config_value('llm', 'max_workers', 4)
        self.llm_RPM = self.get_config_value('llm', 'RPM', 1000)
        self.llm_extra_params = self.get_config_value('llm', 'extra_params', {})
        if self.llm_extra_params is None:
            self.llm_extra_params = {}
        if not isinstance(self.llm_extra_params, dict):
            raise ValueError('llm.extra_params must be a mapping')

        self.ai_news_url = self.get_config_value('ai_news', 'url', None)
        self.ai_news_schedule = self.get_config_value('ai_news', 'schedule', None)
        self.ai_news_prompts = self.get_config_value('ai_news', 'prompts', None)

        self.feeds_status_enabled = self.get_config_value('feeds_status', 'enabled', False)
        self.feeds_status_url = self.get_config_value('feeds_status', 'url', self.ai_news_url)
        self.feeds_status_schedule = self.get_config_value('feeds_status', 'schedule', '09:00')

        self.youtube_enabled = self.get_config_value('youtube', 'enabled', False)
        self.youtube_languages = self.get_config_value('youtube', 'languages', ['zh-Hans', 'zh', 'en'])
        self.youtube_proxy = self.get_config_value('youtube', 'proxy', None)
        self.youtube_timeout = self.get_config_value('youtube', 'timeout', 30)
        self.youtube_max_chars = self.get_config_value('youtube', 'max_chars', None)

        self.categories = self.c.get('categories', {}) or {}
        if not isinstance(self.categories, dict):
            raise ValueError('categories must be a mapping of category title to settings')

        self.llm_routes = self.c.get('llm_routes', {}) or {}
        if not isinstance(self.llm_routes, dict):
            raise ValueError('llm_routes must be a mapping of feed site_url patterns to llm settings')

        # sub-agent definitions (prompts/filters) live outside config.yml:
        # agents_file may point to a single file (mapping of agent name to
        # settings) or to a directory holding one *.yml per agent (the file
        # body is the agent definition, the filename stem is the agent name);
        # entries there override the inline agents
        self.agents_file = self.c.get('agents_file', None)
        inline_agents = self.c.get('agents', {}) or {}
        if self.agents_file:
            agents_path = Path(self.agents_file)
            if agents_path.is_dir():
                file_agents = {}
                agent_files = sorted(agents_path.glob('*.yml')) + sorted(agents_path.glob('*.yaml'))
                for agent_file in agent_files:
                    agent_def = safe_load(agent_file.read_text(encoding='utf8')) or {}
                    if not isinstance(agent_def, dict) or not agent_def:
                        raise ValueError(f'{agent_file} must contain one agent definition mapping')
                    file_agents[agent_file.stem] = agent_def
            else:
                with open(self.agents_file, encoding='utf8') as agents_fh:
                    file_agents = safe_load(agents_fh) or {}
                if not isinstance(file_agents, dict):
                    raise ValueError(f'{self.agents_file} must be a mapping of agent name to settings')
            self.agents = {**inline_agents, **file_agents}
        else:
            self.agents = inline_agents

    def get_config_value(self, section, key, default=None):
        return self.c.get(section, {}).get(key, default)

    def _merge_llm_override(self, override):
        merged = {}
        for key in LLM_CONFIG_KEYS:
            merged[key] = override.get(key, getattr(self, 'llm_' + key))
        if isinstance(override.get('extra_params'), dict):
            merged['extra_params'] = {**(self.llm_extra_params or {}), **override['extra_params']}
        if merged['extra_params'] is None:
            merged['extra_params'] = {}
        if not isinstance(merged['extra_params'], dict):
            raise ValueError('llm extra_params must be a mapping')
        return merged

    def category_llm_config(self, category_title):
        """Return the effective LLM settings for a Miniflux category.

        Merges `categories.<title>.llm` overrides on top of the global `llm`
        section. Returns None when the category has no override, so callers
        keep using the global defaults.
        """
        section = self.categories.get(category_title)
        if not isinstance(section, dict):
            return None
        override = section.get('llm')
        if not isinstance(override, dict) or not override:
            return None
        return self._merge_llm_override(override)

    def route_llm_config(self, site_url):
        """Return LLM settings for the first `llm_routes` pattern (fnmatch on
        the feed site_url) that matches, or None to keep the resolved
        category/global settings."""
        for pattern, override in (self.llm_routes or {}).items():
            if not isinstance(override, dict) or not override:
                continue
            if fnmatch(site_url or '', pattern):
                return self._merge_llm_override(override)
        return None
