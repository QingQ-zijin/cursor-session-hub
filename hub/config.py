"""Installation configuration. Secrets and customer data live outside the app."""
from __future__ import annotations
from dataclasses import dataclass, field
import os
from pathlib import Path
import sys

def default_home() -> Path:
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'CursorSessionHub'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/CursorSessionHub'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'cursor-session-hub'

@dataclass
class Config:
    mode: str = field(default_factory=lambda: os.getenv('CSH_MODE', 'local'))
    home: Path = field(default_factory=lambda: Path(os.getenv('CSH_HOME', str(default_home()))))
    database_url: str = field(default_factory=lambda: os.getenv('CSH_DATABASE_URL', ''))
    local_token: str = field(default_factory=lambda: os.getenv('CSH_LOCAL_TOKEN', ''))
    worker_external: bool = field(default_factory=lambda: os.getenv('CSH_WORKER_EXTERNAL', '0') == '1')
    public_url: str = field(default_factory=lambda: os.getenv('CSH_PUBLIC_URL', ''))
    cookie_secure: bool = field(default_factory=lambda: os.getenv('CSH_COOKIE_SECURE', '1') != '0')
    min_free_bytes: int = field(default_factory=lambda: int(os.getenv('CSH_MIN_FREE_BYTES', str(5 * 1024**3))))
    min_free_ratio: float = field(default_factory=lambda: float(os.getenv('CSH_MIN_FREE_RATIO', '0.10')))
    max_package_bytes: int = 512 * 1024**2
    max_chunk_bytes: int = 4 * 1024**2
    max_queue: int = 50
    max_user_queue: int = 10
    worker_memory_bytes: int = field(default_factory=lambda: int(os.getenv('CSH_WORKER_MEMORY_BYTES', str(512 * 1024**2))))

    def __post_init__(self):
        self.home = Path(self.home).expanduser().resolve()
        if self.mode not in ('local', 'cloud'):
            raise ValueError('CSH_MODE must be local or cloud')
        if not self.database_url:
            self.database_url = 'sqlite:///' + (self.home / 'hub.db').as_posix()

    def prepare(self):
        for name in ('', 'imports', 'contents', 'assets', 'exports', 'uploads', 'bundles', 'raw'):
            (self.home / name).mkdir(parents=True, exist_ok=True)

def get_config() -> Config:
    return Config()
