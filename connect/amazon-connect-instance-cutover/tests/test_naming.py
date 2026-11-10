"""Product naming in the README, docs and UI text: Amazon Connect Customer and AI agents (no AWS)."""
import pathlib
import re

import pytest

import verify_target
from test_repo_hygiene import text_files

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROSE = [ROOT / 'README.md', ROOT / 'frontend' / 'index.html', ROOT / 'docs' / 'images' / 'architecture.dot',
         *sorted((ROOT / 'docs').glob('*.md'))]
OLD_NAME = re.compile(r'Amazon Connect(?! Customer)')
FORMERLY = '(formerly Amazon Connect)'
# The old AI agents product name, built from pieces so this file does not hold it.
OLD_AI_NAME = re.compile(r'\bQ in ' + 'Con' + r'nect\b|\bAmazon ' + r'Q\b', re.I)


@pytest.mark.parametrize('path', PROSE, ids=lambda p: p.name)
def test_amazon_connect_customer_naming(path):
    text = path.read_text().replace(FORMERLY, '')
    assert OLD_NAME.findall(text) == []
    assert 'AWS Connect' not in text


def test_ai_agents_naming_in_every_tracked_file():
    """README, docs, UI, Lambda code and logs, templates, scripts and tests: the old name is gone."""
    offenders = {name: [i + 1 for i, line in enumerate(text.split('\n')) if OLD_AI_NAME.search(line)][:5]
                 for name, text in text_files()}
    assert {name: lines for name, lines in offenders.items() if lines} == {}


def test_first_readme_mention_says_formerly_amazon_connect():
    text = (ROOT / 'README.md').read_text()
    first = text.index('Amazon Connect Customer', text.index('\n'))  # after the title
    assert text[first:].startswith('Amazon Connect Customer ' + FORMERLY)


def test_follow_up_texts_shown_in_the_ui_use_the_new_names():
    for text in verify_target.FOLLOW_UP_ACTIONS.values():
        assert not OLD_AI_NAME.search(text) and not OLD_NAME.search(text)


def test_readme_warns_about_voice_id_and_lists_the_reason_code():
    readme = (ROOT / 'README.md').read_text()
    assert 'Voice ID reached end of support on May 20, 2026, and the tool does not migrate it' in readme
    assert '| `VoiceIdEndOfSupport` |' in readme
    assert set(verify_target.FOLLOW_UP_ACTIONS) <= {line.split('`')[1] for line in readme.split('\n')
                                                    if line.startswith('| `') and line.count('`') >= 2}
