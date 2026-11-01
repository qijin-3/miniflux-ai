import json
import markdown
import threading

from common.config import Config
from common.logger import logger
from core.entry_filter import filter_entry
from core.get_ai_result import get_ai_result
from core.youtube_transcript import fetch_entry_transcript, TransientTranscriptError

config = Config()
file_lock = threading.Lock()

def process_entry(miniflux_client, entry):
    #Todo change to queue
    llm_result = ''

    # resolve applicable agents first (this also implies "not processed yet":
    # the filter skips entries whose content already starts with an agent title)
    applicable = [agent for agent in config.agents.items() if filter_entry(config, agent, entry)]

    # transcripts are fetched only when some agent will consume them; they are
    # LLM input only and never written into the entry content
    transcript_text = None
    if applicable:
        try:
            transcript_text = fetch_entry_transcript(config, entry)
        except TransientTranscriptError as e:
            # YouTube is temporarily blocking us: leave the entry untouched so
            # the next poll retries instead of processing without the transcript
            logger.warning(f"entry {entry['id']}: {e}; deferring to next poll")
            return

    agent_content = entry['content']
    if transcript_text:
        agent_content = transcript_text + '\n\n---\n以下为视频页面原始内容 ---\n' + agent_content

    # llm resolution: llm_routes by feed site_url first, then category
    # override, then the global llm section
    category_title = entry['feed']['category']['title']
    site_url = entry['feed'].get('site_url') or ''
    category_llm = config.route_llm_config(site_url) or config.category_llm_config(category_title)

    for agent in applicable:
        if category_llm:
            logger.info(f"entry {entry['id']}: {site_url or category_title} "
                        f"uses llm {category_llm['model']} @ {category_llm['base_url']}")
        try:
            response_content = get_ai_result(agent[1]["prompt"], agent_content, llm_config=category_llm)
        except Exception as e:
            logger.error(
                f"Error processing entry {entry['id']} with agent {agent[0]}: {e}"
            )
            continue
        log_content = (response_content or "")[:20] + '...' if len(response_content or "") > 20 else response_content
        logger.info(f"agents:{agent[0]} feed_id:{entry['id']} result:{log_content}")

        # save for ai_summary
        if agent[0] == 'summary':
            entry_list = {
                'datetime': entry['created_at'],
                'category': entry['feed']['category']['title'],
                'title': entry['title'],
                'content': response_content,
                'url': entry['url']
            }
            with file_lock:
                try:
                    with open('entries.json', 'r') as file:
                        data = json.load(file)
                except (FileNotFoundError, json.JSONDecodeError):
                    data = []
                data.append(entry_list)
                with open('entries.json', 'w') as file:
                    json.dump(data, file, indent=4, ensure_ascii=False)

        if agent[1]['style_block']:
            llm_result = (llm_result + '<blockquote>\n  <p><strong>'
                          + agent[1]['title'] + '</strong> '
                          + response_content.replace('\n', '').replace('\r', '')
                          + '\n</p>\n</blockquote><br/>')
        else:
            llm_result = llm_result + f"{agent[1]['title']}{markdown.markdown(response_content)}<hr><br />"

    if llm_result:
        dict_result = miniflux_client.update_entry(entry['id'], content= llm_result + entry['content'])
