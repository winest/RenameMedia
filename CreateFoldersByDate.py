import argparse
import datetime
import errno
import logging
import os
import re
import sys
import time
from pathlib import Path


g_reAlreadyRenamed = re.compile(r"^(.*?-)?([0-9]{8})_[0-9]{6}(-.*)?$")
DATE_FOLDER = re.compile(r"(?<![0-9])[0-9]{8}(?![0-9])")
CHINESE_TEXT = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002ffff\U00030000-\U000323af]"
)
MEDIA_EXTENSIONS = {
    ".bmp", ".jpg", ".jpeg", ".png", ".gif", ".heic", ".mp3",
    ".mp4", ".mov", ".m4a", ".avi", ".amr", ".aac", ".flac",
}
DISPOSABLE_METADATA = {".nomedia", "thumbs.db", ".ds_store"}


def GetDateByFileName(path):
    match = g_reAlreadyRenamed.fullmatch(path.stem)
    if match is None:
        raise ValueError(f"File is not in the renamed format: {path}")
    return datetime.datetime.strptime(match.group(2), "%Y%m%d").date()


def IsDateFolder(name):
    for match in DATE_FOLDER.finditer(name):
        try:
            datetime.datetime.strptime(match.group(), "%Y%m%d")
        except ValueError:
            continue
        return True
    return False


def IsOrganizedFolder(name):
    is_year = bool(re.fullmatch(r"[0-9]{4}", name)) and int(name) > 0
    return is_year or bool(CHINESE_TEXT.search(name)) or IsDateFolder(name)


def PlanMoves(root, scanned_dirs=None):
    logging.info("Checking root: %s", root)
    started = time.monotonic()
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")
    if any(IsOrganizedFolder(part) for part in root.parts):
        logging.info("Skipping organized folder (year, date or Chinese name): %s", root)
        return []
    by_date = {}
    pending = [root]
    scanned = eligible = skipped_folders = 0
    last_report = started
    while pending:
        directory = pending.pop()
        if scanned_dirs is not None:
            scanned_dirs.append(directory)
        relative_dir = directory.relative_to(root)
        logging.info(
            "Scanning %s: %d files checked, %d eligible (%.1fs)",
            relative_dir, scanned, eligible, time.monotonic() - started,
        )
        subdirs = []
        # DirEntry reuses directory-listing metadata instead of querying each path.
        with os.scandir(directory) as entries:
            for entry in entries:
                now = time.monotonic()
                if now - last_report >= 5:
                    logging.info(
                        "Scanning %s: %d files checked, %d eligible (%.1fs)",
                        relative_dir, scanned, eligible, now - started,
                    )
                    last_report = now
                if entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction()):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if IsOrganizedFolder(entry.name):
                        skipped_folders += 1
                        logging.info("Skipping organized folder: %s", Path(entry.path).relative_to(root))
                    else:
                        subdirs.append(Path(entry.path))
                    continue
                scanned += 1
                source = Path(entry.path)
                if source.suffix.lower() not in MEDIA_EXTENSIONS:
                    continue
                try:
                    date = GetDateByFileName(source)
                except ValueError as error:
                    logging.warning("Skipping %s: %s", source.relative_to(root), error)
                    continue
                by_date.setdefault(date, []).append(source)
                eligible += 1
        pending.extend(sorted(subdirs, reverse=True))
    logging.info(
        "Scan complete: %d files checked, %d eligible, %d organized folders skipped (%.1fs). Planning groups.",
        scanned, eligible, skipped_folders, time.monotonic() - started,
    )
    moves = []
    for date in sorted(by_date):
        target = root / date.strftime("%Y%m%d")
        # Never add files to an existing date folder, even when the name matches.
        if target.exists():
            logging.warning("Skipping date; destination already exists: %s", target)
            continue
        reserved = set()
        for source in sorted(by_date[date]):
            destination = target / source.name
            count = 1
            while str(destination).casefold() in reserved:
                destination = target / f"{source.stem}-{count}{source.suffix}"
                count += 1
            reserved.add(str(destination).casefold())
            moves.append((source, destination))
    logging.info("Plan complete: %d files to move (%.1fs)", len(moves), time.monotonic() - started)
    return moves


def RemoveEmptyFolders(root, directories, moved_sources=(), dry_run=False):
    removed = set()
    omitted = set(moved_sources) if dry_run else set()
    for directory in reversed(directories):
        if directory == root:
            continue
        relative = directory.relative_to(root)
        if any(IsOrganizedFolder(part) for part in relative.parts):
            continue
        if directory.is_symlink() or (hasattr(directory, "is_junction") and directory.is_junction()):
            continue
        metadata = []
        with os.scandir(directory) as entries:
            has_content = False
            for entry in entries:
                path = Path(entry.path)
                if path in omitted:
                    continue
                if entry.name.casefold() in DISPOSABLE_METADATA and entry.is_file(follow_symlinks=False):
                    metadata.append(path)
                else:
                    has_content = True
            if has_content:
                continue
        for path in metadata:
            if not dry_run:
                path.unlink()
            logging.info("%sRemove metadata file: %s", "Preview: " if dry_run else "", path.relative_to(root))
        if not dry_run:
            try:
                directory.rmdir()
            except OSError as error:
                # Another process may add a file after the empty check.
                if error.errno in (errno.ENOTEMPTY, errno.EEXIST) or getattr(error, "winerror", None) == 145:
                    logging.warning("Keeping folder; no longer empty: %s", relative)
                    continue
                raise
        removed.add(directory)
        omitted.add(directory)
        logging.info("%sRemove empty folder: %s", "Preview: " if dry_run else "", relative)
    return removed


def Organize(root, dry_run=False):
    logging.info("Starting %s: %s", "preview (no files will be moved)" if dry_run else "organization", root)
    scanned_dirs = []
    moves = PlanMoves(root, scanned_dirs)
    root = Path(root).resolve()
    created = set()
    for source, destination in moves:
        if not dry_run:
            if destination.parent not in created:
                destination.parent.mkdir()
                created.add(destination.parent)
            if destination.exists():
                raise FileExistsError(f"Destination exists: {destination}")
            source.rename(destination)
        logging.info(
            "%s%s => %s", "Preview: " if dry_run else "",
            source.relative_to(root), destination.relative_to(root),
        )
    logging.info("Checking empty folders from deepest level to root.")
    removed = RemoveEmptyFolders(root, scanned_dirs, (source for source, _ in moves), dry_run)
    logging.info(
        "%s complete: %d files, %d empty folders %s",
        "Preview" if dry_run else "Organization", len(moves), len(removed),
        "would be removed" if dry_run else "removed",
    )
    return moves


def main():
    parser = argparse.ArgumentParser(description="Group media into one folder per day.")
    parser.add_argument("directory", nargs="?", default=os.getcwd())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    log_dir = Path(__file__).resolve().parent / "Logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / (
        "CreateFoldersByDate-" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".txt"
    )
    logging.basicConfig(
        level=logging.INFO, format="[%(asctime)s][%(levelname)s]: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_path, encoding="utf-8")],
    )
    try:
        Organize(args.directory, args.dry_run)
    except (OSError, ValueError):
        logging.exception("Folder organization failed: %s", args.directory)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
