"""Repository hygiene for publication: no trailing whitespace, final newlines, no .DS_Store (no AWS)."""
import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BINARY_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.ico', '.zip', '.pdf'}
SKIP_DIRS = {'.git', '__pycache__', '.build', '.venv', '.pytest_cache', '.ruff_cache', 'node_modules'}
GENERATED = {'frontend/config.js'}


def tracked_files():
    """Tracked files when this is a git checkout, else every file outside build and cache directories."""
    try:
        out = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-z'], capture_output=True, check=True).stdout
        return [n for n in out.decode().split('\0') if n]
    except (OSError, subprocess.CalledProcessError):
        return [str(p.relative_to(ROOT)) for p in ROOT.rglob('*')
                if p.is_file() and not SKIP_DIRS & set(p.relative_to(ROOT).parts)]


def text_files():
    for name in tracked_files():
        path = ROOT / name
        if name in GENERATED or path.suffix.lower() in BINARY_EXT or path.name == '.DS_Store' or not path.is_file():
            continue
        data = path.read_bytes()
        if b'\0' not in data:
            yield name, data.decode('utf-8')


def test_no_trailing_whitespace():
    offenders = {name: [i + 1 for i, line in enumerate(text.split('\n')) if line != line.rstrip(' \t\r')][:5]
                 for name, text in text_files()}
    assert {name: lines for name, lines in offenders.items() if lines} == {}


def test_every_text_file_ends_with_one_newline():
    offenders = [name for name, text in text_files() if text and (not text.endswith('\n') or text.endswith('\n\n'))]
    assert offenders == []


def test_no_macos_metadata_files():
    assert [n for n in tracked_files() if pathlib.PurePath(n).name == '.DS_Store'] == []


@pytest.mark.parametrize('pattern', ['.DS_Store', '__pycache__/', '.build/'])
def test_gitignore_keeps_local_artifacts_out(pattern):
    assert pattern in (ROOT / '.gitignore').read_text().split('\n')


# Internal review steps (scanner names, numbered scans, test runs and reports, the
# working folder) stay out of the published tree. The scanner and folder words are
# built from parts so this file does not hold them either.
_SCANNER = 'hol' + 'mes'
_FOLDER = 'review ' + 'work' + 'space'
INTERNAL_REFERENCE = re.compile(
    _SCANNER + '|' + _FOLDER + r'|\bscans? \d|\breports/|\be2e\b|\bruns? \d|\brun_\d|\breport ?\d', re.I)


def test_this_file_does_not_name_the_scanner_or_the_folder():
    text = pathlib.Path(__file__).read_text().lower()
    assert _SCANNER not in text and _FOLDER not in text


def test_no_internal_review_references():
    this_file = pathlib.Path(__file__).resolve().relative_to(ROOT).as_posix()
    offenders = {name: [i + 1 for i, line in enumerate(text.split('\n')) if INTERNAL_REFERENCE.search(line)][:5]
                 for name, text in text_files() if name != this_file}
    assert {name: lines for name, lines in offenders.items() if lines} == {}


@pytest.mark.parametrize('name', ['docs/DEMO_PITCH.md', 'docs/TESTING_GUIDE.md'])
def test_removed_docs_stay_removed_and_unlinked(name):
    assert not (ROOT / name).exists()
    this_file = pathlib.Path(__file__).resolve().relative_to(ROOT).as_posix()
    linked = [n for n, text in text_files() if n != this_file and pathlib.PurePath(name).name in text]
    assert linked == []
