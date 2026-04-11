"""
prompts_config.py — Language prompt strings loaded once at startup.
Extracted from main.py so blueprints can import without circular dependency.
"""
import os

_BASE = os.path.dirname(__file__)


def _load_file(name: str) -> str:
    path = os.path.join(_BASE, 'prompts', name)
    try:
        with open(path, encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        return ''


LANE4_LANGUAGE_PROMPT   = _load_file('lane4_language_prompt.txt')
LANE4_DEEP_DIVE_PROMPT  = _load_file('lane4_deep_dive_prompt.txt')
