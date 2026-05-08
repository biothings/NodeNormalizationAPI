import logging
import os
import pathlib
from functools import lru_cache
from typing import Iterable, Optional

from git import InvalidGitRepositoryError, NoSuchPathError, Repo

logger = logging.getLogger(__name__)

UNKNOWN_VERSION = "Unknown"
VERSION_FILE_NAME = "version.txt"
VERSION_FILE_ENV_VAR = "NODENORM_VERSION_FILE"
CONTAINER_VERSION_FILE = pathlib.Path("/home/nodenorm/configuration") / VERSION_FILE_NAME


def read_version_file(version_file_paths: Optional[Iterable[pathlib.Path]] = None) -> Optional[str]:
    """Read the build-time version file when the app is running from a packaged image."""
    candidate_paths = []
    configured_path = os.getenv(VERSION_FILE_ENV_VAR)
    if configured_path:
        candidate_paths.append(pathlib.Path(configured_path))

    if version_file_paths is None:
        candidate_paths.extend([pathlib.Path.cwd() / VERSION_FILE_NAME, CONTAINER_VERSION_FILE])
    else:
        candidate_paths.extend(version_file_paths)

    for version_file_path in candidate_paths:
        try:
            version = version_file_path.read_text(encoding="utf-8").strip()
        except OSError:
            continue

        if version:
            return version

    return None


@lru_cache(maxsize=None)
def get_github_commit_hash(source_path: Optional[pathlib.Path] = None) -> str:
    """Retrieve the current GitHub commit hash using gitpython."""
    try:
        repo_path = source_path or pathlib.Path(__file__).resolve()
        repo = Repo(repo_path, search_parent_directories=True)

        return repo.head.commit.hexsha
    except (InvalidGitRepositoryError, NoSuchPathError) as exc:
        logger.warning("Git repository unavailable for version lookup: %s", exc)
        return UNKNOWN_VERSION
    except Exception as exc:
        logger.exception("Error getting GitHub commit hash: %s", exc)
        return UNKNOWN_VERSION


@lru_cache(maxsize=None)
def get_version() -> str:
    return read_version_file() or get_github_commit_hash()
