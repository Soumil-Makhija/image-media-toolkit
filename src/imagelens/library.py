"""Local originals and editable metadata; no model calls or image rewriting."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import warnings

from PIL import Image, UnidentifiedImageError

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 20_000_000
MAX_IMAGES = 5000
_FORMATS = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"), "WEBP": ("webp", "image/webp")}
_ID = re.compile(r"[0-9a-f]{64}\Z")
_COLUMNS = ("id", "original_sha256", "original_filename", "object_path", "mime", "width", "height",
            "alt_text", "tags", "metadata_source", "review_status", "created_at")


class LibraryError(ValueError):
    """Invalid image, metadata, library or storage operation."""


def _read_regular(path):
    """Open only a regular final component, without following its symlink."""
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_BYTES:
            raise LibraryError("Image must be a regular file of at most 10 MiB")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise LibraryError("Image exceeds the 10 MiB limit")
        return raw
    except OSError as error:
        raise LibraryError("Cannot read image; symlinks and nonregular files are unsupported") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _inspect(raw):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in _FORMATS:
                    raise LibraryError("Only actual PNG, JPEG and WebP images are supported")
                width, height = image.size
                if width < 1 or height < 1 or width * height > MAX_PIXELS:
                    raise LibraryError("Image exceeds the 20-megapixel limit")
                if getattr(image, "n_frames", 1) != 1:
                    raise LibraryError("Only single-frame images are supported")
                image_format = image.format
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
        return (*_FORMATS[image_format], width, height)
    except LibraryError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise LibraryError("Image is invalid, truncated or unsafe to decode") from error


def _metadata(alt_text, tags, source, reviewed):
    if (type(alt_text) is not str or len(alt_text) > 1000
            or any(ord(char) < 32 and char not in "\n\t\r" for char in alt_text)):
        raise LibraryError("Alt text must be text of at most 1000 characters without control codes")
    if type(tags) not in {list, tuple} or len(tags) > 20:
        raise LibraryError("Use at most 20 text tags")
    cleaned, seen = [], set()
    for tag in tags:
        if type(tag) is not str or not 1 <= len(tag.strip()) <= 50 or any(ord(char) < 32 for char in tag):
            raise LibraryError("Tags must contain 1-50 characters without control codes")
        tag = tag.strip()
        if tag.casefold() not in seen:
            cleaned.append(tag)
            seen.add(tag.casefold())
    if type(source) is not str or source not in {"manual", "vision"} or type(reviewed) is not bool:
        raise LibraryError("Metadata source must be manual or vision; reviewed must be a boolean")
    return alt_text, cleaned


class Library:
    """Create/open one explicitly selected local SQLite image library."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.objects = self.directory / "objects"
        self.database = self.directory / "index.sqlite3"
        try:
            if self.directory.is_symlink() or self.objects.is_symlink() or self.database.is_symlink():
                raise LibraryError("Library directories and database must not be symlinks")
            self.directory.mkdir(parents=True, exist_ok=True)
            self.objects.mkdir(exist_ok=True)
            if self.database.exists() and not self.database.is_file():
                raise LibraryError("Library database must be a regular file")
            with self._session() as connection:
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if not tables and version == 0:
                    connection.execute("""CREATE TABLE images (
                        id TEXT PRIMARY KEY, original_sha256 TEXT NOT NULL,
                        original_filename TEXT NOT NULL, object_path TEXT NOT NULL UNIQUE,
                        mime TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
                        alt_text TEXT NOT NULL, tags TEXT NOT NULL, metadata_source TEXT NOT NULL,
                        review_status TEXT NOT NULL, created_at TEXT NOT NULL)""")
                    connection.execute("PRAGMA user_version=1")
                elif tables != {"images"} or version != 1:
                    raise LibraryError("Unsupported library schema; no migration was attempted")
                if tuple(row[1] for row in connection.execute("PRAGMA table_info(images)")) != _COLUMNS:
                    raise LibraryError("Unsupported library table shape")
        except (OSError, sqlite3.Error) as error:
            raise LibraryError("Cannot initialize the selected local image library") from error

    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA trusted_schema=OFF")
        return connection

    @contextmanager
    def _session(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _id(image_id):
        if type(image_id) is not str or not _ID.fullmatch(image_id):
            raise LibraryError("Image ID must be a full lowercase SHA256")

    @staticmethod
    def _record(row):
        result = dict(row)
        try:
            result["tags"] = json.loads(result["tags"])
            _metadata(result["alt_text"], result["tags"], result["metadata_source"], result["review_status"] == "reviewed")
            if (type(result["id"]) is not str or not _ID.fullmatch(result["id"])
                    or result["original_sha256"] != result["id"]
                    or result["review_status"] not in {"reviewed", "needs_review"}
                    or type(result["original_filename"]) is not str
                    or not result["original_filename"]
                    or type(result["width"]) is not int or type(result["height"]) is not int
                    or result["width"] < 1 or result["height"] < 1
                    or result["width"] * result["height"] > MAX_PIXELS):
                raise ValueError("Malformed stored image record")
        except (ValueError, TypeError) as error:
            raise LibraryError("Invalid stored image metadata") from error
        return result

    def add(self, path):
        path = Path(path)
        raw = _read_regular(path)
        extension, mime, width, height = _inspect(raw)
        image_id = hashlib.sha256(raw).hexdigest()
        object_name = f"objects/{image_id}.{extension}"
        destination = self.directory / object_name
        created = False
        connection = None
        temporary = None
        try:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
            if existing is not None:
                record = self._record(existing)
                if self.read_original(image_id) != raw:
                    raise LibraryError("Stored original does not match the imported image")
                connection.rollback()
                return record
            if connection.execute("SELECT count(*) FROM images").fetchone()[0] >= MAX_IMAGES:
                raise LibraryError("Library exceeds the 5000-image limit")
            if destination.exists() or destination.is_symlink():
                if _read_regular(destination) != raw:
                    raise LibraryError("Existing content-addressed object differs from the original")
            else:
                descriptor, temporary = tempfile.mkstemp(prefix=".import-", dir=self.objects)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o444)
                try:
                    os.link(temporary, destination)
                    created = True
                except FileExistsError:
                    if _read_regular(destination) != raw:
                        raise LibraryError("Content-addressed object collision")
            created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            connection.execute("INSERT INTO images VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                image_id, image_id, path.name, object_name, mime, width, height,
                "", "[]", "manual", "needs_review", created_at))
            connection.commit()
            return self.get(image_id)
        except (OSError, sqlite3.Error, LibraryError) as error:
            # Delete a newly published object before releasing our write lock.
            if created and connection is not None and connection.in_transaction:
                destination.unlink(missing_ok=True)
            if connection is not None:
                connection.rollback()
            if isinstance(error, LibraryError):
                raise
            raise LibraryError("Image import failed; no metadata record was saved") from error
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
            if connection is not None:
                connection.close()

    def get(self, image_id):
        self._id(image_id)
        try:
            with self._session() as connection:
                row = connection.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
            if row is None:
                raise LibraryError("Unknown image ID")
            return self._record(row)
        except sqlite3.Error as error:
            raise LibraryError("Cannot read the image library") from error

    def list(self):
        try:
            with self._session() as connection:
                rows = connection.execute("SELECT * FROM images ORDER BY id LIMIT ?", (MAX_IMAGES + 1,)).fetchall()
            if len(rows) > MAX_IMAGES:
                raise LibraryError("Library exceeds the 5000-image limit")
            return [self._record(row) for row in rows]
        except sqlite3.Error as error:
            raise LibraryError("Cannot read the image library") from error

    def edit(self, image_id, alt_text, tags, source="manual", reviewed=True, *, expected_metadata=None):
        self._id(image_id)
        alt_text, tags = _metadata(alt_text, tags, source, reviewed)
        metadata_fields = {"alt_text", "tags", "metadata_source", "review_status"}
        if expected_metadata is not None:
            if (type(expected_metadata) is not dict or set(expected_metadata) != metadata_fields
                    or type(expected_metadata["tags"]) is not list
                    or type(expected_metadata["review_status"]) is not str
                    or expected_metadata["review_status"] not in {"reviewed", "needs_review"}):
                raise LibraryError("Expected metadata must contain the exact saved metadata fields")
            expected_alt, expected_tags = _metadata(
                expected_metadata["alt_text"], expected_metadata["tags"],
                expected_metadata["metadata_source"], expected_metadata["review_status"] == "reviewed")
            if expected_tags != expected_metadata["tags"]:
                raise LibraryError("Expected metadata must use canonical saved tags")
            expected_metadata = {**expected_metadata, "alt_text": expected_alt, "tags": list(expected_tags)}
        try:
            with self._session() as connection:
                # Compare and update under the same write lock. A draft cannot
                # overwrite an edit committed after its baseline was captured.
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
                if row is None:
                    raise LibraryError("Unknown image ID")
                current = self._record(row)
                if expected_metadata is not None and expected_metadata != {
                        field: current[field] for field in metadata_fields}:
                    raise LibraryError("Stale metadata: the image metadata changed after this draft was created")
                result = connection.execute("UPDATE images SET alt_text=?, tags=?, metadata_source=?, review_status=? WHERE id=?", (
                    alt_text, json.dumps(tags, ensure_ascii=False), source,
                    "reviewed" if reviewed else "needs_review", image_id))
                if result.rowcount != 1:
                    raise LibraryError("Unknown image ID")
                saved = self._record(connection.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone())
            return saved
        except sqlite3.Error as error:
            raise LibraryError("Cannot save image metadata") from error

    def search(self, query):
        if type(query) is not str or not 1 <= len(query.strip()) <= 200:
            raise LibraryError("Search query must contain 1-200 characters")
        terms = query.casefold().split()
        return [record for record in self.list() if all(term in "\n".join((
            record["original_filename"], record["alt_text"], *record["tags"])).casefold() for term in terms)]

    def read_original(self, image_id):
        """Return exact original bytes after verifying the content-addressed path."""
        record = self.get(image_id)
        allowed = {f"objects/{image_id}.{extension}" for extension, _ in _FORMATS.values()}
        if record["object_path"] not in allowed or record["original_sha256"] != image_id:
            raise LibraryError("Invalid stored original reference")
        raw = _read_regular(self.directory / record["object_path"])
        if hashlib.sha256(raw).hexdigest() != image_id:
            raise LibraryError("Stored original checksum mismatch")
        extension, mime, width, height = _inspect(raw)
        if (record["object_path"] != f"objects/{image_id}.{extension}"
                or (record["mime"], record["width"], record["height"]) != (mime, width, height)):
            raise LibraryError("Stored original format or dimensions differ from its record")
        return raw
