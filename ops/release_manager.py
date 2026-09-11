#!/usr/bin/env python3
"""Manage project-local production, development, and historical releases."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Sequence


PROJECT_ROOT = Path(
    os.environ.get("ROGUETRADER_PROJECT_ROOT", Path(__file__).resolve().parents[1])
).resolve()
RUNTIME_ROOT = PROJECT_ROOT / ".runtime"
RELEASES_DIR = RUNTIME_ROOT / "releases"
RELEASE_DATA_DIR = RUNTIME_ROOT / "release-data"
PRODUCTION_DIR = RUNTIME_ROOT / "production"
DEVELOPMENT_DIR = RUNTIME_ROOT / "development"
DEVELOPMENT_WORKTREE = DEVELOPMENT_DIR / "worktree"
BIN_DIR = RUNTIME_ROOT / "bin"
LOCKS_DIR = RUNTIME_ROOT / "locks"
UV_CACHE_DIR = RUNTIME_ROOT / "uv-cache"
BASE_PYTHON = Path(
    os.environ.get("ROGUETRADER_BASE_PYTHON", PROJECT_ROOT / ".venv" / "bin" / "python")
).resolve()
CURRENT_LINK = PRODUCTION_DIR / "current"
PREVIOUS_LINK = PRODUCTION_DIR / "previous"
PRODUCTION_ENV = PRODUCTION_DIR / ".env"
DEVELOPMENT_ENV = DEVELOPMENT_DIR / ".env"
METADATA_FILE = ".rogue-release.json"
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ReleaseError(RuntimeError):
    """A safe, user-facing environment management failure."""


def run(
    args: Sequence[str],
    *,
    cwd: Path = PROJECT_ROOT,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        check=check,
        text=True,
        capture_output=capture_output,
        env={**os.environ, "UV_CACHE_DIR": str(UV_CACHE_DIR)},
    )


def validate_version(version: str) -> str:
    if not VERSION_RE.fullmatch(version):
        raise ReleaseError(
            "Version must start with a letter or number and contain only "
            "letters, numbers, dot, underscore, or hyphen."
        )
    return version


def release_path(version: str) -> Path:
    return RELEASES_DIR / validate_version(version)


def release_data_path(version: str) -> Path:
    return RELEASE_DATA_DIR / validate_version(version)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_output(*args: str, cwd: Path = PROJECT_ROOT) -> str:
    result = run(("git", *args), cwd=cwd, capture_output=True)
    return result.stdout.strip()


def python_version(python: Path, *, cwd: Path) -> str:
    result = run((str(python), "--version"), cwd=cwd, capture_output=True)
    return (result.stdout or result.stderr).strip()


def copy_tree_once(source: Path, destination: Path) -> None:
    if destination.exists():
        return
    if source.exists():
        shutil.copytree(source, destination)
    else:
        destination.mkdir(parents=True, exist_ok=True)


def install_launcher() -> None:
    source = PROJECT_ROOT / "ops" / "run-version"
    destination = BIN_DIR / "run-version"
    if not source.is_file():
        raise ReleaseError(f"Version launcher is missing: {source}")
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    temporary = BIN_DIR / f".run-version.{os.getpid()}.tmp"
    shutil.copy2(source, temporary)
    temporary.chmod(0o755)
    os.replace(temporary, destination)


def bootstrap_runtime() -> None:
    for directory in (
        RELEASES_DIR,
        RELEASE_DATA_DIR,
        PRODUCTION_DIR,
        DEVELOPMENT_DIR,
        LOCKS_DIR,
        UV_CACHE_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    source_env = PROJECT_ROOT / ".env"
    if not source_env.is_file():
        raise ReleaseError(f"Project environment file is missing: {source_env}")
    if not BASE_PYTHON.is_file():
        raise ReleaseError(f"Base Python interpreter is missing: {BASE_PYTHON}")
    if not PRODUCTION_ENV.exists():
        shutil.copy2(source_env, PRODUCTION_ENV)
        PRODUCTION_ENV.chmod(0o600)
    if not DEVELOPMENT_ENV.exists():
        shutil.copy2(source_env, DEVELOPMENT_ENV)
        DEVELOPMENT_ENV.chmod(0o600)

    (PROJECT_ROOT / "my_results" / "运行结果").mkdir(parents=True, exist_ok=True)
    install_launcher()


def add_link(path: Path, target: Path) -> None:
    if path.exists() or path.is_symlink():
        raise ReleaseError(f"Path already exists and will not be overwritten: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)


def remove_failed_worktree(path: Path) -> None:
    if not path.exists():
        return
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(path)],
        cwd=PROJECT_ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def prepare_release_state(version: str, target: Path) -> Path:
    state = release_data_path(version)
    state.mkdir(parents=True, exist_ok=False)
    copy_tree_once(
        PROJECT_ROOT / "roguetrader" / "dataflows" / "data_cache",
        state / "data_cache",
    )
    copy_tree_once(PROJECT_ROOT / "roguetrader" / "memory_db", state / "memory_db")
    add_link(target / ".env", PRODUCTION_ENV)
    add_link(target / "my_results", PROJECT_ROOT / "my_results")
    add_link(target / "roguetrader" / "dataflows" / "data_cache", state / "data_cache")
    add_link(target / "roguetrader" / "memory_db", state / "memory_db")
    return state


def install(version: str, git_ref: str) -> None:
    version = validate_version(version)
    target = release_path(version)
    state = release_data_path(version)
    if target.exists() or state.exists():
        raise ReleaseError(f"Release or release data already exists for {version}")

    bootstrap_runtime()
    commit = git_output("rev-parse", "--verify", f"{git_ref}^{{commit}}")
    try:
        run(("git", "worktree", "add", "--detach", str(target), commit))
        prepare_release_state(version, target)
        run(
            (
                "uv",
                "sync",
                "--frozen",
                "--no-dev",
                "--python",
                str(BASE_PYTHON),
            ),
            cwd=target,
        )
        python = target / ".venv" / "bin" / "python"
        if not python.is_file():
            raise ReleaseError(f"Release environment was not created: {python}")

        entrypoint = (
            "my_scripts/daily_analysis.py"
            if (target / "my_scripts" / "daily_analysis.py").is_file()
            else "my_scripts/roguetrader1.py"
        )
        metadata = {
            "schema_version": 1,
            "version": version,
            "git_ref": git_ref,
            "git_commit": commit,
            "entrypoint": entrypoint,
            "installed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "lock_sha256": sha256(target / "uv.lock"),
            "python_version": python_version(python, cwd=target),
            "base_python": str(BASE_PYTHON),
        }
        (target / METADATA_FILE).write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        verify(version, import_check=True)
    except Exception:
        remove_failed_worktree(target)
        if state.exists():
            shutil.rmtree(state)
        raise

    print(f"Installed release {version} at commit {commit[:12]}")


def load_metadata(target: Path) -> dict[str, object]:
    metadata_path = target / METADATA_FILE
    if not metadata_path.is_file():
        raise ReleaseError(f"Release metadata is missing: {metadata_path}")
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ReleaseError(f"Invalid release metadata: {metadata_path}: {exc}") from exc


def same_target(link: Path, target: Path) -> bool:
    return link.is_symlink() and link.resolve() == target.resolve()


def verify(version: str, *, import_check: bool = False) -> None:
    target = release_path(version)
    if not target.is_dir():
        raise ReleaseError(f"Release does not exist: {target}")
    state = release_data_path(version)
    metadata = load_metadata(target)
    expected_commit = str(metadata.get("git_commit", ""))
    actual_commit = git_output("rev-parse", "HEAD", cwd=target)
    if actual_commit != expected_commit:
        raise ReleaseError(
            f"Release commit mismatch: expected {expected_commit}, found {actual_commit}"
        )
    if sha256(target / "uv.lock") != metadata.get("lock_sha256"):
        raise ReleaseError(f"Dependency lock changed in release {version}")
    if git_output("status", "--porcelain", "--untracked-files=no", cwd=target):
        raise ReleaseError(f"Tracked source files changed in release {version}")

    required_links = {
        target / ".env": PRODUCTION_ENV,
        target / "my_results": PROJECT_ROOT / "my_results",
        target / "roguetrader" / "dataflows" / "data_cache": state / "data_cache",
        target / "roguetrader" / "memory_db": state / "memory_db",
    }
    for link, expected in required_links.items():
        if not same_target(link, expected):
            raise ReleaseError(f"Release link is missing or incorrect: {link}")

    entrypoint = target / str(metadata.get("entrypoint", ""))
    python = target / ".venv" / "bin" / "python"
    if not entrypoint.is_file():
        raise ReleaseError(f"Release entrypoint is missing: {entrypoint}")
    if not os.access(python, os.X_OK):
        raise ReleaseError(f"Release Python is not executable: {python}")
    actual_python_version = python_version(python, cwd=target)
    if actual_python_version != metadata.get("python_version"):
        raise ReleaseError(
            "Release Python changed: "
            f"expected {metadata.get('python_version')}, found {actual_python_version}"
        )
    run(("uv", "pip", "check", "--python", str(python)), cwd=target)
    if import_check:
        run(
            (
                str(python),
                "-c",
                "from roguetrader.graph.trading_graph import RogueTraderGraph; "
                "print('RogueTrader import check: OK')",
            ),
            cwd=target,
        )
    print(f"Verified release {version} at commit {actual_commit[:12]}")


def rebuild_environment(version: str) -> None:
    target = release_path(version)
    metadata = load_metadata(target)
    if git_output("status", "--porcelain", "--untracked-files=no", cwd=target):
        raise ReleaseError(f"Tracked source files changed in release {version}")
    if sha256(target / "uv.lock") != metadata.get("lock_sha256"):
        raise ReleaseError(f"Dependency lock changed in release {version}")
    run(
        (
            "uv",
            "sync",
            "--frozen",
            "--no-dev",
            "--python",
            str(BASE_PYTHON),
        ),
        cwd=target,
    )
    python = target / ".venv" / "bin" / "python"
    metadata["python_version"] = python_version(python, cwd=target)
    metadata["base_python"] = str(BASE_PYTHON)
    (target / METADATA_FILE).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    verify(version, import_check=True)
    print(f"Rebuilt release environment {version} from its frozen lock")


def atomic_link(link: Path, target: Path) -> None:
    temporary = link.parent / f".{link.name}.{os.getpid()}.tmp"
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(os.path.relpath(target, link.parent))
    os.replace(temporary, link)


def activate(version: str) -> None:
    bootstrap_runtime()
    target = release_path(version)
    verify(version)
    old_target = CURRENT_LINK.resolve() if CURRENT_LINK.is_symlink() else None
    if old_target is not None and old_target != target.resolve():
        atomic_link(PREVIOUS_LINK, old_target)
    atomic_link(CURRENT_LINK, target)
    print(f"Activated project production release {version}")


def rollback() -> None:
    if not PREVIOUS_LINK.is_symlink():
        raise ReleaseError("No previous project release is available for rollback.")
    previous = PREVIOUS_LINK.resolve()
    if previous.parent != RELEASES_DIR.resolve():
        raise ReleaseError(f"Previous release points outside the release directory: {previous}")
    activate(previous.name)


def setup_development(branch: str, base_ref: str) -> None:
    bootstrap_runtime()
    if DEVELOPMENT_WORKTREE.exists():
        raise ReleaseError(f"Development worktree already exists: {DEVELOPMENT_WORKTREE}")
    branch_exists = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=PROJECT_ROOT,
        check=False,
    ).returncode == 0
    args = ["git", "worktree", "add", str(DEVELOPMENT_WORKTREE)]
    if branch_exists:
        args.append(branch)
    else:
        args.extend(("-b", branch, base_ref))
    try:
        run(args)
        add_link(DEVELOPMENT_WORKTREE / ".env", DEVELOPMENT_ENV)
        run(
            ("uv", "sync", "--frozen", "--python", str(BASE_PYTHON)),
            cwd=DEVELOPMENT_WORKTREE,
        )
    except Exception:
        remove_failed_worktree(DEVELOPMENT_WORKTREE)
        raise
    print(f"Development worktree ready: {DEVELOPMENT_WORKTREE} ({branch})")


def status() -> None:
    current = CURRENT_LINK.resolve() if CURRENT_LINK.is_symlink() else None
    previous = PREVIOUS_LINK.resolve() if PREVIOUS_LINK.is_symlink() else None
    production_branch = git_output("branch", "--show-current") or "detached"
    production_commit = git_output("rev-parse", "--short=12", "HEAD")
    production_dirty = bool(git_output("status", "--porcelain"))
    print(f"project.production.current: {current.name if current else 'not-activated'}")
    print(f"project.production.previous: {previous.name if previous else 'none'}")
    print(f"project.control.branch: {production_branch}")
    print(f"project.control.commit: {production_commit}")
    print(f"project.control.dirty: {'yes' if production_dirty else 'no'}")
    if DEVELOPMENT_WORKTREE.is_dir():
        dev_branch = git_output("branch", "--show-current", cwd=DEVELOPMENT_WORKTREE)
        dev_commit = git_output("rev-parse", "--short=12", "HEAD", cwd=DEVELOPMENT_WORKTREE)
        dev_dirty = bool(git_output("status", "--porcelain", cwd=DEVELOPMENT_WORKTREE))
        print(f"project.development.branch: {dev_branch or 'detached'}")
        print(f"project.development.commit: {dev_commit}")
        print(f"project.development.dirty: {'yes' if dev_dirty else 'no'}")
    else:
        print("project.development: not-created")


def list_releases() -> None:
    if not RELEASES_DIR.exists():
        print("No releases installed.")
        return
    current = CURRENT_LINK.resolve() if CURRENT_LINK.is_symlink() else None
    previous = PREVIOUS_LINK.resolve() if PREVIOUS_LINK.is_symlink() else None
    for target in sorted(path for path in RELEASES_DIR.iterdir() if path.is_dir()):
        try:
            commit = str(load_metadata(target).get("git_commit", "unknown"))[:12]
        except ReleaseError:
            commit = "invalid"
        markers = []
        if current == target.resolve():
            markers.append("current")
        if previous == target.resolve():
            markers.append("previous")
        suffix = f" [{', '.join(markers)}]" if markers else ""
        print(f"{target.name}  {commit}{suffix}")


def sync_environment(destination: Path, label: str) -> None:
    source = PROJECT_ROOT / ".env"
    if not source.is_file():
        raise ReleaseError(f"Project environment file is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        backup = destination.parent / (
            destination.name + ".backup-" + datetime.now().strftime("%Y%m%d-%H%M%S")
        )
        shutil.copy2(destination, backup)
        backup.chmod(0o600)
    temporary = destination.parent / f".{destination.name}.{os.getpid()}.tmp"
    shutil.copy2(source, temporary)
    temporary.chmod(0o600)
    os.replace(temporary, destination)
    print(f"{label} environment updated; the previous file was backed up.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    install_parser = subparsers.add_parser("install", help="Install an immutable release.")
    install_parser.add_argument("version")
    install_parser.add_argument("git_ref")
    verify_parser = subparsers.add_parser("verify", help="Verify an installed release.")
    verify_parser.add_argument("version")
    verify_parser.add_argument("--import-check", action="store_true")
    activate_parser = subparsers.add_parser(
        "activate", help="Atomically select the release used by the stable project entrypoint."
    )
    activate_parser.add_argument("version")
    subparsers.add_parser("rollback", help="Atomically select the previous release.")
    dev_parser = subparsers.add_parser(
        "setup-development", help="Create the isolated development worktree and environment."
    )
    dev_parser.add_argument("--branch", default="develop")
    dev_parser.add_argument("--base-ref", default="main")
    rebuild_parser = subparsers.add_parser(
        "rebuild-environment", help="Rebuild one release environment from its frozen lock."
    )
    rebuild_parser.add_argument("version")
    subparsers.add_parser("status", help="Show all project-local environments.")
    subparsers.add_parser("list", help="List installed historical releases.")
    subparsers.add_parser("bootstrap", help="Initialize project-local runtime state.")
    subparsers.add_parser("sync-production-env", help="Refresh the project production env.")
    subparsers.add_parser("sync-development-env", help="Refresh the development env.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "install":
            install(args.version, args.git_ref)
        elif args.command == "verify":
            verify(args.version, import_check=args.import_check)
        elif args.command == "activate":
            activate(args.version)
        elif args.command == "rollback":
            rollback()
        elif args.command == "setup-development":
            setup_development(args.branch, args.base_ref)
        elif args.command == "rebuild-environment":
            rebuild_environment(args.version)
        elif args.command == "status":
            status()
        elif args.command == "list":
            list_releases()
        elif args.command == "bootstrap":
            bootstrap_runtime()
            print(f"Project runtime initialized at {RUNTIME_ROOT}")
        elif args.command == "sync-production-env":
            sync_environment(PRODUCTION_ENV, "Production")
        elif args.command == "sync-development-env":
            sync_environment(DEVELOPMENT_ENV, "Development")
        return 0
    except (ReleaseError, subprocess.CalledProcessError) as exc:
        print(f"environment error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
