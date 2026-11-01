import threading

from markdownify import markdownify as md
from ratelimit import limits, sleep_and_retry

from openai import OpenAI
from google import genai
from google.genai import types
from common.config import Config, LLM_CONFIG_KEYS
from common.logger import logger

config = Config()

_client_cache = {}
_client_cache_lock = threading.Lock()
_rate_limiter_cache = {}
_rate_limiter_cache_lock = threading.Lock()


def _effective_llm_config(llm_config):
    """Fill unspecified keys from the global llm section."""
    if not llm_config:
        llm_config = {}
    effective = {}
    for key in LLM_CONFIG_KEYS:
        effective[key] = llm_config.get(key, getattr(config, 'llm_' + key))
    if effective['extra_params'] is None:
        effective['extra_params'] = {}
    return effective


def _build_client(effective):
    provider = effective['provider'] or 'openai'
    if provider == 'gemini':
        return genai.Client(
            http_options=types.HttpOptions(base_url=effective['base_url']),
            api_key=effective['api_key'],
        )
    return OpenAI(base_url=effective['base_url'], api_key=effective['api_key'])


def _get_client(effective):
    cache_key = (effective['provider'] or 'openai', effective['base_url'], effective['api_key'])
    with _client_cache_lock:
        if cache_key not in _client_cache:
            _client_cache[cache_key] = _build_client(effective)
        return _client_cache[cache_key]


def _get_rate_limiter(effective):
    """One RPM bucket per provider credentials, so categories routed to
    different models/keys do not throttle each other."""
    rpm = effective['RPM'] or 1000
    cache_key = (effective['provider'] or 'openai', effective['base_url'], effective['api_key'], rpm)
    with _rate_limiter_cache_lock:
        if cache_key not in _rate_limiter_cache:
            @sleep_and_retry
            @limits(calls=rpm, period=60)
            def run_under_limit(fn, *args, **kwargs):
                return fn(*args, **kwargs)
            _rate_limiter_cache[cache_key] = run_under_limit
        return _rate_limiter_cache[cache_key]


def _invoke_openai(effective, prompt, request):
    if "${content}" in prompt:
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {
                "role": "user",
                "content": prompt.replace("${content}", md(request)),
            },
        ]
    else:
        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": "The following is the input content:\n---\n "
                + md(request),
            },
        ]

    client = _get_client(effective)
    completion = client.chat.completions.create(
        model=effective['model'],
        messages=messages,
        timeout=effective['timeout'],
        **effective['extra_params'],
    )
    return completion.choices[0].message.content


def _invoke_gemini(effective, prompt, request):
    if "${content}" in prompt:
        instruction = ["You are a helpful assistant."]
        contents = prompt.replace("${content}", md(request))
    else:
        instruction = [prompt]
        contents = "The following is the input content:\n---\n " + md(request)

    client = _get_client(effective)
    response = client.models.generate_content(
        model=effective['model'],
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=instruction,
            **effective['extra_params'],
        ),
    )
    return response.text


def get_ai_result(prompt: str, request: str, llm_config: dict = None):
    """Run a prompt against an LLM.

    llm_config optionally carries per-category overrides (provider, base_url,
    api_key, model, timeout, max_length, RPM, extra_params); anything missing
    falls back to the global llm section.
    """
    effective = _effective_llm_config(llm_config)

    if effective['max_length'] and len(request) > effective['max_length']:
        request = request[: effective['max_length']]

    if (effective['provider'] or 'openai') == 'gemini':
        invoke = _invoke_gemini
    else:
        invoke = _invoke_openai

    try:
        return _get_rate_limiter(effective)(invoke, effective, prompt, request)
    except Exception as e:
        logger.error(f"Error in get_ai_result ({effective['provider'] or 'openai'}): {e}")
        raise
