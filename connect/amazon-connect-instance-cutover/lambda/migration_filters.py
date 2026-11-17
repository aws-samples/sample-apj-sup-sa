"""
What the migration copies on purpose, and what it skips on purpose.

One module, imported by the list and create steps (the migration side) and
by VerifyTarget (the verify side), so both always agree on what counts as
migrated. tests/test_migration_filters.py fails if any side stops using it.

Every predicate returns True for an item the migration copies. Each one
takes the dict the list step filters on: a List... or Search... summary,
except evaluation forms, whose status is only in DescribeEvaluationForm.
"""

# Contact flows every instance already has (Connect creates them).
DEFAULT_FLOW_PREFIXES = ('Default ', 'Sample ')
# Predefined attributes Connect owns.
SYSTEM_ATTRIBUTE_PREFIX = 'connect:'
# Agent statuses every instance has (Available and Offline).
BUILT_IN_AGENT_STATUS_TYPES = frozenset({'ROUTABLE', 'OFFLINE'})
# AWS managed views have this name prefix.
AWS_VIEW_PREFIX = 'AWS '

# Request parameters of the list calls (both sides send the same ones).
QUEUE_LIST_PARAMS = {'QueueTypes': ['STANDARD']}   # agent queues belong to users
VIEW_LIST_PARAMS = {'Type': 'CUSTOMER_MANAGED'}     # AWS managed views exist in every instance
VOCABULARY_SEARCH_PARAMS = {'State': 'ACTIVE'}
AI_LIST_PARAMS = {'origin': 'CUSTOMER'}             # AI agents and AI prompts: SYSTEM ones are built in


def feature_on(features, name):
    """True only for a boolean true flag: the state machine Choice states use BooleanEquals true."""
    return (features or {}).get(name) is True


def migrates_contact_flow(item):
    return not (item.get('Name') or '').startswith(DEFAULT_FLOW_PREFIXES)


def migrates_queue(item):
    return item.get('QueueType', 'STANDARD') == 'STANDARD'


def migrates_view(item):
    """Customer managed, published and not named like an AWS managed view."""
    return (item.get('Type', 'CUSTOMER_MANAGED') == VIEW_LIST_PARAMS['Type']
            and not (item.get('Name') or '').startswith(AWS_VIEW_PREFIX)
            and item.get('Status') == 'PUBLISHED')


def migrates_vocabulary(item):
    return item.get('State') == VOCABULARY_SEARCH_PARAMS['State']


def migrates_rule(item):
    return item.get('PublishStatus') == 'PUBLISHED'


def migrates_predefined_attribute(item):
    return not (item.get('Name') or '').startswith(SYSTEM_ATTRIBUTE_PREFIX)


def migrates_agent_status(item):
    return item.get('Type') not in BUILT_IN_AGENT_STATUS_TYPES


def migrates_evaluation_form(form):
    """Takes the DescribeEvaluationForm result (the summary has no status): active forms only."""
    return form.get('Status') == 'ACTIVE'
