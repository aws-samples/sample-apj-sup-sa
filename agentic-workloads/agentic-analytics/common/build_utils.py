"""
Build utilities for React app and deployment artifacts.
Shared between package scripts and manual deploy scripts.
"""
import boto3
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path


def get_s3_client(region=None):
    """Get S3 client."""
    region = region or os.environ.get('AWS_REGION', 'us-west-2')
    return boto3.client('s3', region_name=region)


def build_react_app(ui_dir, env_vars=None):
    """Build the React application.
    
    Args:
        ui_dir: Path to UI directory
        env_vars: Optional dict of environment variables for build
    
    Returns:
        Path: Build directory path
    """
    ui_dir = Path(ui_dir)
    print(f"Building React application in {ui_dir}")

    # Merge environment variables into the .env file for the build. We MERGE rather
    # than overwrite so a partial env-file (e.g. just REACT_APP_VOICE_SIGNALING_URL on
    # a voice redeploy) cannot silently drop config a previous deploy already wrote
    # (REACT_APP_AGENT_RUNTIME_ARN, Cognito vars, ...). New keys win; existing keys not
    # present in env_vars are preserved.
    if env_vars:
        env_file = ui_dir / ".env"
        merged = {}
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        merged[k.strip()] = v.strip()
        merged.update(env_vars)
        with open(env_file, "w") as f:
            for key, value in merged.items():
                f.write(f"{key}={value}\n")
        print(f"  Wrote {len(merged)} env vars to .env "
              f"({len(env_vars)} new/updated, {len(merged) - len(env_vars)} preserved)")
    
    # Run npm install
    result = subprocess.run(
        ["npm", "install"],
        cwd=ui_dir,
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        raise Exception(f"npm install failed: {result.stderr}")
    
    # Run npm build
    result = subprocess.run(
        ["npm", "run", "build"],
        cwd=ui_dir,
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        raise Exception(f"npm build failed: {result.stderr}")
    
    build_dir = ui_dir / "build"
    print(f"Build complete: {build_dir}")
    return build_dir


def create_deployment_zip(source_dir, output_path=None):
    """Create a ZIP file of a directory.
    
    Args:
        source_dir: Directory to zip
        output_path: Optional output path (uses temp file if not provided)
    
    Returns:
        str: Path to ZIP file
    """
    source_dir = Path(source_dir)
    print(f"Creating deployment ZIP from {source_dir}")
    
    if output_path is None:
        # mkstemp (not the deprecated, race-prone mktemp) atomically creates the
        # file and returns an open fd; close it since we re-open by path to write.
        fd, output_path = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
    
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file_path in source_dir.rglob('*'):
            if file_path.is_file():
                arcname = file_path.relative_to(source_dir)
                zipf.write(file_path, arcname)
    
    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"Created ZIP: {output_path} ({size_mb:.2f} MB)")
    return output_path


def upload_to_s3(local_path, bucket, key, region=None):
    """Upload file to S3.
    
    Args:
        local_path: Local file path
        bucket: S3 bucket name
        key: S3 object key
        region: AWS region
    
    Returns:
        str: S3 URI
    """
    s3 = get_s3_client(region)
    
    print(f"Uploading to s3://{bucket}/{key}")
    s3.upload_file(local_path, bucket, key)
    
    return f"s3://{bucket}/{key}"


def build_and_upload_ui(ui_dir, bucket, key, env_vars=None, region=None):
    """Build React app and upload to S3.
    
    Args:
        ui_dir: Path to UI directory
        bucket: S3 bucket name
        key: S3 object key for ZIP
        env_vars: Optional environment variables for build
        region: AWS region
    
    Returns:
        str: S3 URI of uploaded ZIP
    """
    build_dir = build_react_app(ui_dir, env_vars)
    zip_path = create_deployment_zip(build_dir)
    
    try:
        return upload_to_s3(zip_path, bucket, key, region)
    finally:
        os.unlink(zip_path)
