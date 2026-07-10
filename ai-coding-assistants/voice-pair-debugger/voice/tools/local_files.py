import os
from pathlib import Path


def _safe_path(file_path: str) -> Path:
    """Resolve a path and ensure it stays within the working directory."""
    cwd = Path.cwd().resolve()
    resolved = (cwd / file_path).resolve()
    if not resolved.is_relative_to(cwd):
        raise ValueError(f"Access denied: {file_path} is outside the project directory.")
    return resolved


async def read_file(params, file_path: str):
    """Read the contents of a file in the local project directory.

    Args:
        file_path: Relative path to the file (e.g. src/handler.py).
    """
    try:
        path = _safe_path(file_path)
        content = path.read_text()
        # Truncate very large files to keep context manageable
        if len(content) > 8000:
            content = content[:8000] + "\n... [truncated]"
        await params.result_callback({"path": file_path, "content": content})
    except (ValueError, FileNotFoundError, PermissionError) as e:
        await params.result_callback({"error": str(e)})


async def list_files(params, directory: str = ".", max_depth: int = 3):
    """List files in the project directory tree.

    Args:
        directory: Relative directory to list. Defaults to project root.
        max_depth: How many levels deep to traverse. Default 3.
    """
    try:
        base = _safe_path(directory)
        if not base.is_dir():
            await params.result_callback({"error": f"{directory} is not a directory."})
            return

        files = []
        cwd = Path.cwd().resolve()
        for root, dirs, filenames in os.walk(base):
            depth = len(Path(root).relative_to(base).parts)
            if depth >= max_depth:
                dirs.clear()
                continue
            # Skip common noise
            dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__", ".venv", "venv")]
            for f in filenames:
                rel = Path(root, f).relative_to(cwd)
                files.append(str(rel))

        await params.result_callback({"directory": directory, "files": files[:100]})
    except (ValueError, PermissionError) as e:
        await params.result_callback({"error": str(e)})
