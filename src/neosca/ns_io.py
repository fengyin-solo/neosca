#!/usr/bin/env python3

import contextlib
import glob
import json
import logging
import lzma
import os
import os.path as os_path
import pickle
import stat
import sys
import tempfile
import zipfile
from collections.abc import Generator, Iterable, Sequence
from datetime import datetime, timezone
from os import PathLike
from pathlib import Path, PurePath
from typing import Any
from xml.etree.ElementTree import XML, fromstring

from charset_normalizer import detect

from neosca.ns_consts import CACHE_DIR, CACHE_INFO_PATH
from neosca.ns_utils import Ns_Procedure_Result


class Ns_IO_Meta(type):
    def __new__(cls, name, bases, dict_):
        dict_["SUPPORTED_EXTENSIONS"] = tuple(
            attr.removeprefix("read_") for attr in dict_ if attr.startswith("read_")
        )
        return super().__new__(cls, name, bases, dict_)


class Ns_IO(metaclass=Ns_IO_Meta):
    # Type checker does not detect definition in Ns_IO_Meta, so declare here to
    # silence the "access unknown member warning"
    SUPPORTED_EXTENSIONS: tuple[str, ...] = tuple()
    HIDDEN_PREFIXES: tuple[str, ...] = (".", "~$")

    DOCX_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    DOCX_PARA = DOCX_NAMESPACE + "p"
    DOCX_TEXT = DOCX_NAMESPACE + "t"

    ODT_NAMESPACE = ".//{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
    ODT_PARA = ODT_NAMESPACE + "p"

    previous_encoding: str = "utf-8"

    @classmethod
    def read_txt(cls, path: str, is_guess_encoding: bool = True) -> str:
        if not is_guess_encoding:
            with open(path, encoding="utf-8") as f:
                return f.read()

        try:
            logging.info(f"Attempting to read {path} with {cls.previous_encoding} encoding...")
            with open(path, encoding=cls.previous_encoding) as f:
                content = f.read()
        except UnicodeDecodeError:
            logging.info(f"Attempt failed. Guessing the encoding of {path}...")
            with open(path, "rb") as f:
                bytes_ = f.read()

            encoding = detect(bytes_)["encoding"]
            assert isinstance(encoding, str), f"Got invalid encoding for {path}: {encoding}"

            logging.info(f"Decoding the byte string with {encoding} encoding...")
            content = bytes_.decode(encoding=encoding)
            cls.previous_encoding = encoding

        return content

    @classmethod
    def read_docx(cls, path: str) -> str:
        """
        Take the path of a docx file as argument, return the text in unicode.
        This approach does not extract text from headers and footers.

        https://etienned.github.io/posts/extract-text-from-word-docx-simply/
        """
        with zipfile.ZipFile(path) as zip_file:
            xml_content = zip_file.read("word/document.xml")
        tree = XML(xml_content)

        paragraphs = []
        for paragraph in tree.iter(cls.DOCX_PARA):
            text = "".join(node.text for node in paragraph.iter(cls.DOCX_TEXT) if node.text)
            paragraphs.append(text)
        return "\n".join(paragraphs)

    @classmethod
    def read_odt(cls, path: str) -> str:
        with zipfile.ZipFile(path) as zip_file:
            xml_content = zip_file.read("content.xml")
        root = fromstring(xml_content)
        paragraphs = root.findall(cls.ODT_PARA)
        return "\n".join("".join(node.itertext()) for node in paragraphs)

    @classmethod
    def suffix(cls, file_path: str | PathLike, *, strip_dot: bool = False) -> str:
        """
        >>> suffix('my/library/setup.py')
        .py
        >>> suffix('my/library.tar.gz')
        .gz
        >>> suffix('my/library.tar.gz', strip_dot=True)
        gz
        >>> suffix('my/library')
        ''
        """
        extension = os_path.splitext(file_path)[-1]
        if strip_dot:
            extension = extension.lstrip(".")
        return extension

    @classmethod
    def supports(cls, file_path: str | PathLike) -> bool:
        # Can instead use hasattr(f"read_{extension}").
        # The SUPPORTED_EXTENSIONS is required by ns_main_cli:74 to list
        #  supported extensions to users.
        return cls.suffix(file_path, strip_dot=True) in cls.SUPPORTED_EXTENSIONS

    @classmethod
    def not_supports(cls, file_path: str | PathLike) -> bool:
        return not cls.supports(file_path)

    @classmethod
    def load_file(cls, file_path: str) -> str:
        extension = cls.suffix(file_path, strip_dot=True)
        if extension not in cls.SUPPORTED_EXTENSIONS:
            raise ValueError(f"{file_path} is of unsupported filetype. Skipping.")

        return getattr(cls, f"read_{extension}")(file_path)

    @classmethod
    def is_writable(cls, filename: str) -> Ns_Procedure_Result:
        """Check whether files are opened by other processes such as WPS"""
        if not os_path.exists(filename):
            return True, None
        try:
            with open(filename, "w", encoding="utf-8"):
                pass
        except PermissionError:
            return (
                False,
                (
                    f"PermissionError: can not write to {filename}, because it is already in use"
                    " by another process.\n\n1. Ensure that {filename} is closed, or \n2."
                    " Specify another output filename through the `-o` option, e.g. nsca"
                    f" input.txt -o {filename.replace('.csv', '-2.csv')}"
                ),
            )
        else:
            return True, None

    @classmethod
    def _check_file_extension(cls, file_path: str | PathLike, valid_extensions: str | tuple[str, ...]):
        if not os_path.isfile(file_path):
            raise FileNotFoundError(f"File {file_path} does not exist")
        if not str(file_path).endswith(valid_extensions):
            raise ValueError(f"{file_path} does not have a valid extension")

    @classmethod
    def load_pickle_lzma(cls, file_path: str | PathLike) -> Any:
        cls._check_file_extension(file_path, (".pickle.lzma", ".pkl.lzma"))

        with open(file_path, "rb") as f:
            data_pickle_lzma = f.read()

        data_pickle = lzma.decompress(data_pickle_lzma)
        return pickle.loads(data_pickle)

    @classmethod
    def load_pickle(cls, file_path: str | PathLike) -> Any:
        cls._check_file_extension(file_path, (".pickle", ".pkl"))

        with open(file_path, "rb") as f:
            data_pickle = f.read()
        return pickle.loads(data_pickle)

    @classmethod
    def load_lzma(cls, file_path: str | PathLike) -> bytes:
        cls._check_file_extension(file_path, ".lzma")

        with open(file_path, "rb") as f:
            data_lzma = f.read()
        return lzma.decompress(data_lzma)

    @classmethod
    def load_json(cls, file_path: str | PathLike) -> Any:
        cls._check_file_extension(file_path, ".json")

        with open(file_path, "rb") as f:
            return json.load(f)

    @classmethod
    def dump_json(cls, data: Any, path: str | PathLike) -> None:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except FileNotFoundError:
            Path(path).parent.mkdir(parents=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    @classmethod
    def dump_bytes(cls, data: bytes, path: str | PathLike) -> None:
        try:
            with open(path, "wb") as f:
                f.write(data)
        except FileNotFoundError:
            Path(path).parent.mkdir(parents=True)
            with open(path, "wb") as f:
                f.write(data)

    @classmethod
    def get_verified_ifile_list(cls, ifile_list: Iterable[str]) -> list[str]:
        verified_ifile_list = []
        for path in ifile_list:
            # File path
            if os_path.isfile(path):
                if cls.not_supports(path):
                    logging.warning(f"{path} is of unsupported filetype. Skipping.")
                    continue
                logging.debug(f"Adding {path} to input file list")
                verified_ifile_list.append(path)
            # Dir path
            elif os_path.isdir(path):
                verified_ifile_list.extend(
                    os_path.join(path, file_name)
                    for file_name in next(os.walk(path))[2]
                    if cls.supports(file_name)
                )
            # Glob pattern
            elif glob.glob(path):
                verified_ifile_list.extend(glob.glob(path))
            else:
                logging.critical(f"No such file as\n\n{path}")
                sys.exit(1)
        verified_ifile_list = [
            path for path in verified_ifile_list if not os_path.basename(path).startswith(cls.HIDDEN_PREFIXES)
        ]
        return verified_ifile_list

    @classmethod
    def get_verified_subfiles_list(cls, subfiles_list: list[list[str]]) -> list[list[str]]:
        verified_subfiles_list = []
        for subfiles in subfiles_list:
            verified_subfiles: list[str] = cls.get_verified_ifile_list(subfiles)
            if len(verified_subfiles) == 1:
                logging.critical(
                    f"Only 1 subfile provided: ({verified_subfiles.pop()}). There should be 2"
                    " or more subfiles to combine."
                )
                sys.exit(1)
            verified_subfiles_list.append(verified_subfiles)
        return verified_subfiles_list

    @classmethod
    def ensure_unique_filestem(cls, stem: str, existing_stems: Sequence[str]) -> str:
        if stem in existing_stems:
            occurrence = 2
            while f"{stem} ({occurrence})" in existing_stems:
                occurrence += 1
            stem = f"{stem} ({occurrence})"
        return stem

    @classmethod
    def find_files(cls, folder_path: str, is_recursive: bool) -> list[str]:
        file_paths: list[str] = []
        if is_recursive:
            file_paths.extend(
                os_path.join(dir_path, file_name)
                for dir_path, _, file_names in os.walk(folder_path)
                for file_name in file_names
                if not file_name.startswith(Ns_IO.HIDDEN_PREFIXES)
            )
        else:
            file_paths.extend(
                os_path.join(folder_path, file_name)
                for file_name in next(os.walk(folder_path))[2]
                if not file_name.startswith(Ns_IO.HIDDEN_PREFIXES)
            )
        return file_paths


class Ns_Cache:
    CACHE_EXTENSION = ".pickle.lzma"

    STATUS_VALID = "valid"
    STATUS_MISSING = "missing"
    STATUS_OUTDATED = "outdated"
    STATUS_ORPHANED = "orphaned"
    ALL_STATUSES: tuple[str, ...] = (STATUS_VALID, STATUS_MISSING, STATUS_OUTDATED, STATUS_ORPHANED)
    PRUNABLE_STATUSES: tuple[str, ...] = (STATUS_MISSING, STATUS_OUTDATED, STATUS_ORPHANED)

    # fpath_cname: { "/absolute/path/to/foo.txt": "foo.pickle.lzma", ... }
    fpath_cname: dict[str, str] = (
        Ns_IO.load_json(CACHE_INFO_PATH)
        if CACHE_INFO_PATH.exists() and os_path.getsize(CACHE_INFO_PATH) > 0
        else {}
    )
    info_changed: bool = False

    @classmethod
    def get_cache_path(cls, file_path: str) -> tuple[str, bool]:
        """
        return (cache_path, available: whether the cache is usable)
        """
        if not os_path.isfile(file_path):
            raise FileNotFoundError(f"{file_path} is not an existing file")
        cache_name = cls.fpath_cname.get(file_path, None)
        if file_path not in cls.fpath_cname:
            cache_name = cls.register_cache_name(file_path)
            cache_path = cls._name2path(cache_name)
            return cache_path, False

        cache_name = cls.fpath_cname[file_path]
        cache_path = cls._name2path(cache_name)
        if not os_path.exists(cache_path):
            return cache_path, False
        outdated = os_path.getmtime(cache_path) <= os_path.getmtime(file_path)
        if outdated:
            return cache_path, False
        empty = os_path.getsize(cache_path) == 0
        if empty:
            return cache_path, False

        logging.info(f"Found cache: {cache_path} exists, and is non-empty and newer than {file_path}.")
        return cache_path, True

    @classmethod
    def _size_fmt(cls, filesize: int | float, suffix: str = "B") -> str:
        # https://github.com/gaogaotiantian/viztracer/blob/3ecd46aa0e70df7dd78f720a2660d6da211c4a51/src/viztracer/util.py#L12
        for unit in ("", "Ki", "Mi", "Gi"):
            if abs(filesize) < 1024.0:
                return f"{filesize:3.1f} {unit}{suffix}"
            filesize /= 1024.0
        return f"{filesize:.1f} {'Ti'}{suffix}"

    @classmethod
    def yield_cname_cpath_csize_fpath(cls) -> Generator[tuple[str, str, str, str], None, None]:
        for file_path, cache_name in Ns_Cache.fpath_cname.items():
            cache_path = Ns_Cache._name2path(cache_name)
            if not os_path.exists(cache_path):
                continue
            cache_size = cls._size_fmt(os_path.getsize(cache_path))
            yield cache_name, cache_path, cache_size, file_path

    # ------------------------------------------------------------------
    # Cache inspection and pruning
    # ------------------------------------------------------------------
    @classmethod
    def inspect_entry(cls, input_path: str, cache_name: str) -> dict[str, Any]:
        """Inspect one metadata entry and determine its cache status.

        Statuses:
            valid:    cache file exists, is a non-empty regular file newer
                      than the input file, and the input file still exists
            missing:  metadata references a cache file that does not exist
            outdated: both files exist but the cache is empty or not newer
                      than the input file
            orphaned: the input file no longer exists
        """
        cache_path = cls._name2path(cache_name)
        input_exists = os_path.exists(input_path)

        # lstat/lexists do not follow symbolic links, so a dangling or
        # malicious link is still reported as present but never trusted.
        cache_stat: os.stat_result | None = None
        if os_path.lexists(cache_path):
            try:
                cache_stat = os.lstat(cache_path)
            except OSError:
                cache_stat = None
        cache_exists = cache_stat is not None
        is_symlink = cache_stat is not None and stat.S_ISLNK(cache_stat.st_mode)
        is_regular_file = cache_stat is not None and stat.S_ISREG(cache_stat.st_mode)

        cache_size: int | None = cache_stat.st_size if cache_stat is not None else None
        cache_mtime_epoch: float | None = cache_stat.st_mtime if cache_stat is not None else None
        cache_mtime: str | None = (
            datetime.fromtimestamp(cache_stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
            if cache_stat is not None
            else None
        )

        is_orphaned = not input_exists
        is_outdated = False
        if cache_exists and input_exists:
            # Mirror the validity rules in get_cache_path(): an empty cache
            # or a cache not newer than the input is outdated.
            is_outdated = cache_stat.st_size == 0 or cache_stat.st_mtime <= os_path.getmtime(input_path)  # type: ignore[union-attr]

        if not cache_exists:
            status = cls.STATUS_MISSING
        elif is_orphaned:
            status = cls.STATUS_ORPHANED
        elif is_outdated:
            status = cls.STATUS_OUTDATED
        else:
            status = cls.STATUS_VALID

        return {
            "input_path": input_path,
            "cache_name": cache_name,
            "cache_path": cache_path,
            "cache_exists": cache_exists,
            "input_exists": input_exists,
            "cache_size": cache_size,
            "cache_mtime": cache_mtime,
            "cache_mtime_epoch": cache_mtime_epoch,
            "is_outdated": is_outdated,
            "is_orphaned": is_orphaned,
            "is_symlink": is_symlink,
            "is_regular_file": is_regular_file,
            "status": status,
        }

    @classmethod
    def list_entries(cls) -> list[dict[str, Any]]:
        """Return all metadata entries, sorted by input path."""
        entries = [
            cls.inspect_entry(input_path, cache_name) for input_path, cache_name in cls.fpath_cname.items()
        ]
        return sorted(entries, key=lambda entry: entry["input_path"])

    @classmethod
    def get_cache_dir(cls) -> str:
        return str(CACHE_DIR)

    @classmethod
    def get_cache_info_path(cls) -> str:
        return str(CACHE_INFO_PATH)

    @classmethod
    def _is_within_cache_root(cls, cache_path: str) -> Ns_Procedure_Result:
        """Ensure cache_path stays inside CACHE_DIR without following links.

        Guards against absolute names, parent-directory (``..``) escapes and
        nested paths stored in a tampered cache_info file. Containment is
        checked on the parent directory only, so a symbolic link is never
        resolved while validating its location.
        """
        raw = PurePath(cache_path)
        cache_name = cls._path2name(cache_path)
        if os_path.isabs(cache_name) or ".." in raw.parts or cache_name != raw.name:
            return False, f"unsafe cache name outside the cache root: {cache_name}"

        root = Path(CACHE_DIR).resolve()
        try:
            resolved_parent = Path(cache_path).parent.resolve()
            resolved_parent.relative_to(root)
        except (OSError, ValueError):
            return False, f"cache path escapes the cache root ({root}): {cache_path}"
        return True, None

    @classmethod
    def _check_deletable(cls, cache_path: str) -> Ns_Procedure_Result:
        """Validate that cache_path is safe to delete.

        Only regular files located inside the cache root are deletable.
        Symbolic links are never followed and never removed.
        """
        success, err_msg = cls._is_within_cache_root(cache_path)
        if not success:
            return False, err_msg

        if not os_path.lexists(cache_path):
            # Nothing on disk to remove (e.g. an already-missing cache file).
            return True, None

        cache_stat = os.lstat(cache_path)
        if stat.S_ISLNK(cache_stat.st_mode):
            return False, f"refuse to delete a symbolic link: {cache_path}"
        if not stat.S_ISREG(cache_stat.st_mode):
            return False, f"refuse to delete a non-regular file: {cache_path}"
        return True, None

    @classmethod
    def prune_entries(cls, statuses: Sequence[str], *, apply: bool) -> dict[str, Any]:
        """Prune cache entries belonging to the given statuses.

        Without ``apply`` this is a dry run: no file or metadata is changed.
        With ``apply``, validated cache files are deleted and cache_info is
        rewritten atomically once. Entries whose deletion fails keep their
        metadata and are reported under ``failed``.
        """
        normalized_statuses = tuple(dict.fromkeys(statuses))
        selected = [entry for entry in cls.list_entries() if entry["status"] in normalized_statuses]

        report_entries: list[dict[str, Any]] = []
        for entry in selected:
            cache_path = entry["cache_path"]
            success, err_msg = cls._check_deletable(cache_path)
            if not success:
                report_entries.append({**entry, "action": "failed", "reason": err_msg})
                continue

            if not apply:
                report_entries.append({**entry, "action": "would_delete", "reason": None})
                continue

            if entry["cache_exists"]:
                try:
                    os.remove(cache_path)
                except OSError as exc:
                    report_entries.append(
                        {**entry, "action": "failed", "reason": f"{type(exc).__name__}: {exc}"}
                    )
                    continue
            report_entries.append({**entry, "action": "deleted", "reason": None})

        metadata_updated = False
        if apply:
            removed_inputs = {entry["input_path"] for entry in report_entries if entry["action"] == "deleted"}
            # Only rewrite when at least one entry was successfully removed,
            # and always retain the metadata of failed entries.
            if removed_inputs:
                original_mapping = dict(cls.fpath_cname)
                new_mapping = {
                    input_path: cache_name
                    for input_path, cache_name in original_mapping.items()
                    if input_path not in removed_inputs
                }
                try:
                    cls._dump_cache_info_atomic(new_mapping)
                except OSError as exc:
                    report_entries.append(
                        {
                            "input_path": "",
                            "cache_name": "",
                            "cache_path": cls.get_cache_info_path(),
                            "cache_exists": None,
                            "input_exists": None,
                            "cache_size": None,
                            "cache_mtime": None,
                            "cache_mtime_epoch": None,
                            "is_outdated": None,
                            "is_orphaned": None,
                            "is_symlink": None,
                            "is_regular_file": None,
                            "status": None,
                            "action": "failed",
                            "reason": f"failed to atomically update cache info: {exc}",
                        }
                    )
                else:
                    cls.fpath_cname = new_mapping
                    cls.info_changed = False
                    metadata_updated = True

        summary = {
            "selected": len(report_entries),
            "would_delete": sum(entry["action"] == "would_delete" for entry in report_entries),
            "deleted": sum(entry["action"] == "deleted" for entry in report_entries),
            "failed": sum(entry["action"] == "failed" for entry in report_entries),
        }
        return {
            "apply": apply,
            "statuses": list(normalized_statuses),
            "cache_dir": cls.get_cache_dir(),
            "cache_info": cls.get_cache_info_path(),
            "metadata_updated": metadata_updated,
            "summary": summary,
            "entries": report_entries,
        }

    @classmethod
    def _dump_cache_info_atomic(cls, data: dict[str, str]) -> None:
        """Write cache_info atomically via a temp file and os.replace()."""
        info_path = Path(CACHE_INFO_PATH)
        info_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=info_path.parent, prefix=f".{info_path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, info_path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

    @classmethod
    def _stem2name(cls, stem: str) -> str:
        """
        >>> _stem2name("foo")
        foo.pickle.lzma
        """
        return f"{stem}{cls.CACHE_EXTENSION}"

    @classmethod
    def _name2stem(cls, name: str) -> str:
        """
        >>> _name2stem("foo.pickle.lzma")
        foo
        """
        return name.removesuffix(cls.CACHE_EXTENSION)

    @classmethod
    def _name2path(cls, name: str) -> str:
        """
        >>> _name2path("foo.pickle.lzma")
        /path/to/cache_dir/foo.pickle.lzma
        """
        return str(CACHE_DIR / name)

    @classmethod
    def _path2name(cls, path: str) -> str:
        """
        >>> _path2name("/path/to/cache_dir/foo.pickle.lzma")
        foo.pickle.lzma
        """
        return os_path.basename(path)

    @classmethod
    def register_cache_name(cls, file_path: str) -> str:
        logging.debug(f"Registering cache path for {file_path}...")
        cache_stem = Path(file_path).stem
        cache_stem = Ns_IO.ensure_unique_filestem(
            cache_stem, tuple(map(cls._name2stem, cls.fpath_cname.values()))
        )
        cache_name = cls._stem2name(cache_stem)
        cls.fpath_cname[file_path] = cache_name
        if not cls.info_changed:
            cls.info_changed = True
        return cache_name

    @classmethod
    def delete_cache_entries(cls, deleted_cache_paths: Iterable[str]) -> None:
        logging.debug(f"Deleting cache entries from {CACHE_INFO_PATH}...")
        deleted_cache_names = tuple(map(cls._path2name, deleted_cache_paths))
        cls.fpath_cname = {k: v for k, v in cls.fpath_cname.items() if v not in deleted_cache_names}
        if not cls.info_changed:
            cls.info_changed = True

    @classmethod
    def save_cache_info(cls) -> None:
        if cls.info_changed:
            logging.debug(f"Saving cache information to {CACHE_INFO_PATH}...")
            cls._dump_cache_info_atomic(cls.fpath_cname)
        else:
            logging.debug("No new cache information to save.")
