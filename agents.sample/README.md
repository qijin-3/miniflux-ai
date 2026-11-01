# agents.d 目录说明

每个 `*.yml` 文件定义一个 agent（文件名即 agent 名），config.yml 顶层用
`agents_file: agents.d` 指向本目录。字段：

- `title`：结果块标题（同时作为"已处理"标记，处理过的条目不会重复处理）
- `prompt`：提示词；含 `${content}` 时内容嵌入 user 消息，否则整段作为 system 指令
- `style_block`：true = blockquote 紧凑样式；false = Markdown 渲染（长文推荐）
- `allow_list` / `deny_list`：feed 的 site_url 通配符（fnmatch），二选一生效

注意：名为 `summary` 的 agent 的结果会额外写入 entries.json，供 AI 日报功能汇总，
因此文件摘要类 agent 必须命名为 `summary.yml`。

agent 用哪个 LLM 由 config.yml 决定：`llm_routes`（按源 URL）> `categories`（按分组）> `llm`（全局）。
