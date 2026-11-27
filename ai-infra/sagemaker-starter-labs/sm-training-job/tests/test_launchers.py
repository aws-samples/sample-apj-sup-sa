"""Exercise both MNIST launchers without AWS calls or data downloads.

Run from either MNIST project: uv run --group dev pytest ../tests
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
ROLE = "arn:aws:iam::123456789012:role/mnist-lab"


@pytest.fixture(params=["hello-mnist", "byoc-mnist"])
def launcher(request, monkeypatch):
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    spec = importlib.util.spec_from_file_location("launcher", ROOT / request.param / "launch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_role_fails_before_any_aws_or_data_work(launcher, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["launch.py"])
    client = Mock(side_effect=AssertionError("AWS must not be called"))
    monkeypatch.setattr(launcher.boto3, "client", client)
    monkeypatch.setattr(launcher.boto3.session, "Session", client)
    if hasattr(launcher, "prepare_mnist_data"):
        monkeypatch.setattr(launcher, "prepare_mnist_data", client)
    with pytest.raises(SystemExit, match="Set ROLE"):
        launcher.main()
    client.assert_not_called()


@pytest.mark.parametrize(
    "config,aws_region,default_region,expected",
    [
        ("ap-south-1", "ap-northeast-1", "ap-southeast-2", "ap-south-1"),
        (None, "ap-northeast-1", "ap-southeast-2", "ap-northeast-1"),
        (None, "", "ap-southeast-2", "ap-southeast-2"),
        (None, "", "", "eu-west-1"),
    ],
)
def test_dry_run_region_precedence_without_role(
    launcher, monkeypatch, capsys, config, aws_region, default_region, expected
):
    monkeypatch.setattr(sys, "argv", ["launch.py", "--dry-run"])
    monkeypatch.setattr(launcher, "REGION", config)
    monkeypatch.setenv("AWS_REGION", aws_region)
    monkeypatch.setenv("AWS_DEFAULT_REGION", default_region)
    monkeypatch.setattr(
        launcher.boto3.session, "Session", lambda: SimpleNamespace(region_name="eu-west-1")
    )
    monkeypatch.setattr(
        launcher.boto3, "client",
        lambda service: SimpleNamespace(get_caller_identity=lambda: {"Account": "123456789012"})
        if service == "sts" else pytest.fail(f"Unexpected service: {service}"),
    )
    if hasattr(launcher, "image_uris"):
        monkeypatch.setattr(launcher.image_uris, "retrieve", lambda **kwargs: "pytorch-image")
    monkeypatch.setattr(launcher, "ModelTrainer", Mock(side_effect=AssertionError("No job")))
    launcher.main()
    assert f"region   : {expected}" in capsys.readouterr().out


def test_submission_uses_explicit_role_and_records_returned_job(launcher, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["launch.py"])
    monkeypatch.setattr(launcher, "ROLE", ROLE)
    monkeypatch.setattr(launcher, "REGION", "ap-northeast-1")
    monkeypatch.setattr(launcher, "HERE", tmp_path)
    monkeypatch.setattr(launcher, "WAIT", False)
    monkeypatch.setattr(launcher, "Session", lambda **kwargs: SimpleNamespace())
    monkeypatch.setattr(
        launcher.boto3, "client",
        lambda service: SimpleNamespace(get_caller_identity=lambda: {"Account": "123456789012"})
        if service == "sts" else pytest.fail(f"Unexpected service: {service}"),
    )
    if hasattr(launcher, "image_uris"):
        monkeypatch.setattr(launcher.image_uris, "retrieve", lambda **kwargs: "pytorch-image")
    else:
        monkeypatch.setattr(launcher, "prepare_mnist_data", lambda: str(tmp_path))
    trainer = Mock()
    trainer.with_metric_definitions.return_value = trainer
    trainer._latest_training_job = SimpleNamespace(training_job_name="mnist-test-job")
    factory = Mock(return_value=trainer)
    monkeypatch.setattr(launcher, "ModelTrainer", factory)
    launcher.main()
    assert factory.call_args.kwargs["role"] == ROLE
    trainer.train.assert_called_once()
    assert json.loads((tmp_path / ".last_job.json").read_text()) == {
        "job_name": "mnist-test-job", "region": "ap-northeast-1"
    }


@pytest.mark.parametrize(
    "aws_region,default_region,configured,expected",
    [
        ("ap-northeast-1", "ap-southeast-2", "eu-west-1", "ap-northeast-1"),
        ("", "ap-southeast-2", "eu-west-1", "ap-southeast-2"),
        ("", "", "eu-west-1", "eu-west-1"),
        ("", "", "", None),
    ],
)
def test_build_script_region_selection(
    tmp_path, aws_region, default_region, configured, expected
):
    calls = tmp_path / "calls.jsonl"
    stub = f"#!{sys.executable}\n" + r'''
import json
import os
import sys
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["FAKE_CALLS"], "a") as f:
    f.write(json.dumps([name, *args]) + "\n")
if name == "aws":
    if args[:2] == ["sts", "get-caller-identity"]:
        print("123456789012")
    elif args[:3] == ["configure", "get", "region"]:
        print(os.environ["CONFIGURED_REGION"])
    elif args[:2] == ["ecr", "get-login-password"]:
        print("fake-password")
elif args and args[0] == "login":
    sys.stdin.read()
'''
    for name in ["aws", "docker"]:
        p = tmp_path / name
        p.write_text(stub)
        p.chmod(0o755)
    result = subprocess.run(
        ["bash", "build_and_push.sh"], cwd=ROOT / "byoc-mnist",
        env={
            **os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "AWS_REGION": aws_region, "AWS_DEFAULT_REGION": default_region,
            "CONFIGURED_REGION": configured, "FAKE_CALLS": str(calls),
        }, capture_output=True, text=True, timeout=15, check=False,
    )
    invoked = [json.loads(line) for line in calls.read_text().splitlines()]
    if expected is None:
        assert result.returncode != 0
        assert not any(c[0] == "docker" for c in invoked)
    else:
        assert result.returncode == 0, result.stderr
        for c in invoked:
            if c[:2] == ["aws", "ecr"]:
                assert c[c.index("--region") + 1] == expected
        assert f"dkr.ecr.{expected}.amazonaws.com" in result.stdout
