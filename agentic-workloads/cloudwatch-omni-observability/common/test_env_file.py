import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common.env_file import load_env, parse_env, parse_env_value


class EnvFileTest(unittest.TestCase):
    def test_parse_quotes_comments_and_whitespace(self):
        self.assertEqual(parse_env_value('  "quoted value"  # comment'), "quoted value")
        self.assertEqual(parse_env_value("'literal # value' # comment"), "literal # value")
        self.assertEqual(parse_env_value("value#suffix"), "value#suffix")
        self.assertEqual(parse_env_value("#ff0000"), "#ff0000")
        self.assertEqual(parse_env_value("'p$ss'"), "p$ss")
        self.assertEqual(parse_env_value("'$(printf unsafe)'"), "$(printf unsafe)")

    def test_empty_assignment_and_whitespace_around_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("OMNI_EMPTY=\nOMNI_COMMENTED= # set me\nOMNI_TRIMMED = value \n")
            with patch.dict(os.environ, {"OMNI_EMPTY": "from-shell", "OMNI_COMMENTED": "fallback"}, clear=True):
                load_env(path)
                self.assertEqual(parse_env_value(""), "")
                self.assertEqual(os.environ["OMNI_EMPTY"], "from-shell")
                self.assertEqual(os.environ["OMNI_COMMENTED"], "fallback")
                self.assertEqual(os.environ["OMNI_TRIMMED"], "value")

    def test_rejects_unquoted_whitespace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("OMNI_MALFORMED=two words\n")
            with self.assertRaisesRegex(ValueError, "must be quoted"):
                load_env(path)

    def test_accepts_sample_settings(self):
        settings = {
            "RPS": "5",
            "AGENT_SERVICE_NAME": "my-agent",
            "PROMPT_VERSION": "v2",
            "TOOL_TIMEOUT_SECONDS": "3",
            "ORDERS_API_URL": "http://localhost:8001",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("".join(f"{key}={value}\n" for key, value in settings.items()))
            self.assertEqual(dict(parse_env(path)), settings)

    def test_rejects_invalid_keys(self):
        for assignment in ("export FOO=bar", "FOO BAR=x", "1FOO=x", "lowercase=x", "UID=1", "GID=1",
                           "PATH=/tmp", "LD_PRELOAD=x.so", "PYTHONPATH=/tmp"):
            with self.subTest(assignment=assignment), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / ".env"
                path.write_text(f"{assignment}\n")
                with self.assertRaisesRegex(ValueError, "invalid environment key"):
                    load_env(path)

    def test_rejects_non_assignment_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("AWS_REGION us-west-2\n")
            with self.assertRaisesRegex(ValueError, "expected KEY=VALUE"):
                load_env(path)


if __name__ == "__main__":
    unittest.main()
