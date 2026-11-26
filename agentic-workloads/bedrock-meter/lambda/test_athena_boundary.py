"""Guard: enforcement Lambdas must never reach for Athena.

Enforcement (day/week/month block/unblock) reads authoritative DAY rows from DynamoDB; Athena is
analytics-only (model breakdown, hourly trends, >90d history) and lives solely in api-handler.
Coupling an enforcement path to Athena would put a block/unblock decision behind a multi-second,
cost-metered, eventually-delivered query — a latency/cost/availability regression. This test
statically asserts the boundary so a future edit can't quietly reintroduce it.

Run: python3 -m unittest test_athena_boundary   (from the lambda/ dir)
"""
import ast
import pathlib
import unittest

LAMBDA_DIR = pathlib.Path(__file__).resolve().parent

# Lambdas on the enforcement / hot path. They must not import common.athena or boto3 athena.
ENFORCEMENT_LAMBDAS = ['usage-aggregator', 'periodic-quota-check']


def _imports(source: str) -> set[str]:
    """All imported module paths in a source file (module-level + nested)."""
    names: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _athena_client_calls(source: str) -> list[str]:
    """boto3.client('athena') / boto3.resource('athena') call sites, if any."""
    hits: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ('client', 'resource')
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == 'athena'
        ):
            hits.append(f'line {node.lineno}')
    return hits


class TestAthenaBoundary(unittest.TestCase):
    def test_enforcement_lambdas_do_not_import_athena(self):
        for name in ENFORCEMENT_LAMBDAS:
            source = (LAMBDA_DIR / name / 'index.py').read_text()
            imports = _imports(source)
            offending = {m for m in imports if 'athena' in m.lower()}
            self.assertEqual(
                offending, set(),
                f'{name} must not import Athena (enforcement reads DDB, not Athena): {offending}',
            )

    def test_enforcement_lambdas_do_not_create_athena_client(self):
        for name in ENFORCEMENT_LAMBDAS:
            source = (LAMBDA_DIR / name / 'index.py').read_text()
            hits = _athena_client_calls(source)
            self.assertEqual(
                hits, [],
                f'{name} must not create an Athena boto3 client: {hits}',
            )

    def test_api_handler_is_the_only_athena_consumer(self):
        # Sanity anchor: api-handler IS allowed to use Athena (analytics routes).
        source = (LAMBDA_DIR / 'api-handler' / 'index.py').read_text()
        self.assertIn('common.athena', _imports(source))


if __name__ == '__main__':
    unittest.main()
