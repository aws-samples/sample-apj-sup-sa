"""Scope: a one time cutover, stated in the README, the UI and every migration report (no AWS)."""
import json
import pathlib
import re

import migration_report
from test_migration_report import EXECUTION_ARN, FakeSfn, history

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROSE = [ROOT / 'README.md', ROOT / 'frontend' / 'index.html', *sorted((ROOT / 'docs').glob('*.md'))]
# The only allowed uses of these words are the ones that say what the tool is not.
NEGATIONS = ('not a warm standby, a backup or a multi Region setup', 'does not keep the two instances in sync',
             'does not keep the instances in sync', 'the instances are not kept in sync', 'the tool is not a backup',
             'not a backup)')
FORBIDDEN = re.compile(r'standby|\bbackups?\b|disaster|\bDR\b|failover|fail over|in sync|ongoing sync|'
                       r'continuous(ly)? (sync|replicat)', re.I)


def test_scope_is_in_every_report_path():
    built = migration_report.build_report(FakeSfn(history(('CreateQueues', 'ok', {'queueMapping': {}}))),
                                          EXECUTION_ARN, cleanup_status='DELETED')
    assert built['scope'] == migration_report.SCOPE
    stored = dict(built, status='SUCCEEDED')
    stored.pop('scope')
    events = history(('BuildMigrationReport', 'ok', stored))
    assert migration_report.build_report(FakeSfn(events), EXECUTION_ARN, cleanup_status='DELETED')['scope'] == \
        migration_report.SCOPE


def test_scope_text_says_one_time_cutover_and_names_acgr():
    message = migration_report.SCOPE['message']
    assert migration_report.SCOPE['kind'] == 'ONE_TIME_CUTOVER'
    for phrase in ('one time cutover', 'source instance is retired', 'not a warm standby, a backup or a multi Region',
                   'not kept in sync', 'Global Resiliency (ACGR)', 'reduced service quotas'):
        assert phrase in message
    assert not re.search(r'\d{12}|arn:', message)


def test_readme_and_ui_state_the_scope():
    readme = (ROOT / 'README.md').read_text()
    assert '## Scope: a one time cutover' in readme and 'retire the source instance afterwards' in readme
    assert 'AWS supports only Amazon Connect Customer Global Resiliency (ACGR)' in readme
    assert 'reduced service quotas' in readme
    html = (ROOT / 'frontend' / 'index.html').read_text()
    assert 'id="scopeNote"' in html and 'One time cutover.' in html
    assert '${escapeHtml(result.scope.message)}' in html


def test_no_dr_standby_or_sync_wording():
    offenders = {}
    for path in PROSE:
        text = path.read_text()
        for phrase in NEGATIONS:
            text = text.replace(phrase, '')
        found = FORBIDDEN.findall(text)
        if found:
            offenders[path.name] = found
    assert offenders == {}


def test_report_json_has_no_names():
    assert json.loads(json.dumps(migration_report.SCOPE)) == migration_report.SCOPE


def test_replicate_instance_comparison_leads_with_the_support_plan():
    readme = (ROOT / 'README.md').read_text()
    section = readme.split('### This tool or ReplicateInstance (Global Resiliency)', 1)[1].split('\n#', 1)[0]
    items = [line for line in section.split('\n') if re.match(r'\d\. ', line)]
    assert len(items) == 5
    assert 'AWS Enterprise Support or AWS Unified Operations' in items[0]
    for item, phrase in zip(items[1:], ('SAML 2.0 on a production instance', 'supported Region pair',
                                        'Same account only', 'Bidirectional sync')):
        assert phrase in item
    assert readme.index('## Scope: a one time cutover') < readme.index('### This tool or ReplicateInstance')


def test_startup_framing_is_quota_support_and_account_move_not_cost():
    readme = (ROOT / 'README.md').read_text()
    section = readme.split('### Why this matters for startups', 1)[1].split('\n## ', 1)[0]
    items = [line for line in section.split('\n') if re.match(r'\d\. ', line)]
    assert [re.match(r'\d\. \*\*(.+?)\*\*', item).group(1) for item in items] == [
        'Quota first ordering', 'No Enterprise Support', 'Founder account to production account']
    assert not re.search(r'runway|cheap|low cost|lower cost|save money|savings|inexpensive|budget', readme, re.I)
