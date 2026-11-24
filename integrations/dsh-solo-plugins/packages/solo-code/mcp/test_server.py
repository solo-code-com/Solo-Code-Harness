"""Unit tests for the Solo-Code MCP server's pure helpers.

They spawn nothing: routing, path confinement, and argv construction are pure.
Run with `python -m unittest test_server` from this directory.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import server  # noqa: E402


class RoutingTest(unittest.TestCase):
    def test_known_task_types_route_to_expected_workers(self) -> None:
        self.assertEqual(server.route_for('simple')[0], 'opencode')
        self.assertEqual(server.route_for('complex')[0], 'opencode')
        self.assertEqual(server.route_for('review')[0], 'opencode')
        self.assertEqual(server.route_for('verify')[0], 'dsh')

    def test_unknown_task_type_falls_back(self) -> None:
        self.assertEqual(server.route_for('nonsense'), server.FALLBACK_ROUTE)


class ConfinementTest(unittest.TestCase):
    def test_relative_path_stays_under_root(self) -> None:
        resolved = server.confined_path('tools')
        self.assertIn(server.ROOT.resolve(), resolved.parents)

    def test_parent_escape_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            server.confined_path('..')


class ArgvTest(unittest.TestCase):
    def test_delegate_argv_pins_directory_and_never_enables_writes(self) -> None:
        argv = server.delegate_argv('opencode', 'brief', None, 60)
        self.assertIn('--timeout', argv)
        self.assertIn('60', argv)
        self.assertNotIn('--allow-write', argv)
        self.assertNotIn('--auto-approve', argv)

    def test_opencode_is_pinned_to_the_workspace_root(self) -> None:
        argv = server.delegate_argv('opencode', 'brief', 'model/x', 30)
        self.assertIn('--directory', argv)
        self.assertIn(str(server.ROOT), argv)
        self.assertIn('--model', argv)


class EvidenceTest(unittest.TestCase):
    def test_evidence_rows_name_claim_command_and_output(self) -> None:
        rows = server.evidence_table('opencode', ['python', 'tools/codex_delegate.py', 'brief'], {'exitCode': 0, 'durationMs': 12, 'stdout': 'done'})
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(set(row), {'claim', 'command', 'output'})
        self.assertEqual(rows[0]['output'], 'exit=0 durationMs=12')
        self.assertEqual(rows[1]['output'], 'done')


if __name__ == '__main__':
    unittest.main()
