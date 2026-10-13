"""Bundle the space handler with a current boto3, without Docker.

The Lambda runtime's built-in boto3 has no cloudwatchomni client yet, so the
handler ships a hash-verified dependency lock. Bundling runs `pip install` on
the host, and falls back to the Docker bundling image only if local bundling
fails.
"""

import shutil
import subprocess  # nosec B404 - bundling executes a fixed pip argument vector without a shell
import sys
from pathlib import Path

import jsii
from aws_cdk import BundlingOptions, DockerImage, ILocalBundling
from aws_cdk import aws_lambda as lambda_

BUNDLE_REQUIREMENTS = "requirements-bundle.txt"


@jsii.implements(ILocalBundling)
class LocalPipBundling:
    def __init__(self, source: Path):
        self._source = source

    def try_bundle(self, output_dir: str, *, image=None, **_kwargs) -> bool:
        try:
            subprocess.run(  # nosec B603 - arguments are fixed; only CDK's output path varies
                [sys.executable, "-m", "pip", "install", "--quiet", "--no-compile",
                 "--require-hashes", "--platform", "manylinux2014_aarch64", "--only-binary=:all:",
                 "--python-version", "3.13", "--target", output_dir,
                 "--requirement", str(self._source / BUNDLE_REQUIREMENTS)],
                check=True,
            )
            for item in self._source.iterdir():
                if item.is_file():
                    shutil.copy2(item, output_dir)
            return True
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"local bundling failed ({exc}); falling back to Docker", file=sys.stderr)
            return False


def handler_code(source: Path) -> lambda_.Code:
    return lambda_.Code.from_asset(
        str(source),
        bundling=BundlingOptions(
            image=DockerImage.from_registry("public.ecr.aws/sam/build-python3.13"),
            command=[
                "bash", "-c",
                "pip install --require-hashes --only-binary=:all: "
                f"-r /asset-input/{BUNDLE_REQUIREMENTS} -t /asset-output && cp -r . /asset-output",
            ],
            local=LocalPipBundling(source),
        ),
    )
