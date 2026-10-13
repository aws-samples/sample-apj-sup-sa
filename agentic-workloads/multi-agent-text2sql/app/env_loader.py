"""Environment variable loader with .env file support."""

import os
from pathlib import Path
from typing import Dict, Any


class EnvLoader:
    """Load environment variables from .env file and system environment."""

    def __init__(self):
        self.env_vars = {}
        self._load_env_file()
        self._load_system_env()

    def _load_env_file(self) -> None:
        """Load variables from .env file if it exists."""
        env_file = Path(".env")
        if not env_file.exists():
            return

        try:
            with open(env_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue

                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")
                        self.env_vars[key] = value
        except Exception as e:
            print(f"Warning: Could not read .env file: {e}")

    def _load_system_env(self) -> None:
        """Load system environment variables (they override .env file)."""
        for key, value in os.environ.items():
            self.env_vars[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Get environment variable value."""
        return self.env_vars.get(key, default)

    def get_bool(self, key: str, default: bool = False) -> bool:
        """Get boolean environment variable."""
        value = self.get(key, default)
        if isinstance(value, str):
            return value.lower() in ('true', '1', 'yes', 'on')
        return bool(value)


    def get_debug_settings(self) -> Dict[str, Any]:
        """Get debug and logging settings."""
        return {
            'debug_logging': self.get_bool('DEBUG_LOGGING', False),
            'log_level': self.get('LOG_LEVEL', 'INFO'),
        }