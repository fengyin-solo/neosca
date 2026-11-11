#!/usr/bin/env python3

import os.path as os_path
from datetime import datetime, timezone
from typing import Any

from neosca.ns_about import __version__
from neosca.ns_io import Ns_IO


class Ns_Manifest:
    """
    Build and persist an atomic JSON run manifest.

    The manifest only holds JSON basic types (dict, list, str, int, float,
    bool, None), keeps a stable field/input ordering, and never stores raw
    "--text" input -- only a fixed identifier and its SHA-256 digest.
    """

    TEXT_IDENTIFIER = "cli_text"
    STATUS_SUCCESS = "success"
    STATUS_FAILURE = "failure"

    def __init__(self, analyzer: str, manifest_path: str, options: dict[str, Any]) -> None:
        self.analyzer = analyzer
        self.manifest_path = os_path.abspath(manifest_path)
        # Keep an explicit, alphabetically sorted option order.
        self.options: dict[str, Any] = {key: options[key] for key in sorted(options)}

        self._file_records: dict[str, dict[str, Any]] = {}
        self._text_record: dict[str, str] | None = None

        self.start_time: str | None = None
        self.end_time: str | None = None
        self.status: str | None = None
        self.error: dict[str, str] | None = None
        self.output: dict[str, str] | None = None

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def start(self) -> None:
        self.start_time = self._now_iso()

    def record_text(self, text: str) -> None:
        """Record direct --text input without retaining the raw text."""
        if self._text_record is not None:
            return
        self._text_record = {
            "kind": "text",
            "identifier": self.TEXT_IDENTIFIER,
            "sha256": Ns_IO.sha256_bytes(text.encode("utf-8")),
        }

    def record_file(self, file_path: str) -> dict[str, Any]:
        abs_path = os_path.abspath(file_path)
        record = self._file_records.get(abs_path)
        if record is None:
            record = {
                "kind": "file",
                "path": abs_path,
                "size": os_path.getsize(abs_path),
                "sha256": Ns_IO.sha256_file(abs_path),
                "cache_path": None,
                "cache_hit": False,
            }
            self._file_records[abs_path] = record
        return record

    def record_cache(self, file_path: str, cache_path: str | None, is_hit: bool) -> None:
        record = self.record_file(file_path)
        record["cache_path"] = os_path.abspath(cache_path) if cache_path else None
        record["cache_hit"] = bool(is_hit)

    def mark_success(self, output_path: str) -> None:
        abs_output = os_path.abspath(output_path)
        self.status = self.STATUS_SUCCESS
        self.error = None
        self.output = {
            "path": abs_output,
            "sha256": Ns_IO.sha256_file(abs_output),
        }
        self.end_time = self._now_iso()

    def mark_failure(self, exception: BaseException) -> None:
        self.status = self.STATUS_FAILURE
        self.error = {
            "type": type(exception).__name__,
            "message": str(exception),
        }
        self.output = None
        self.end_time = self._now_iso()

    def _build_inputs(self) -> list[dict[str, Any]]:
        # File records are sorted by absolute path; the text record (if any)
        # always comes last under its fixed identifier.
        inputs = [self._file_records[path] for path in sorted(self._file_records)]
        if self._text_record is not None:
            inputs.append(self._text_record)
        return inputs

    @staticmethod
    def _ensure_json_basic(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError(f"manifest dict keys must be str, got {type(key).__name__}")
                Ns_Manifest._ensure_json_basic(item)
        elif isinstance(value, list):
            for item in value:
                Ns_Manifest._ensure_json_basic(item)
        elif not isinstance(value, (str, int, float, bool)) and value is not None:
            raise TypeError(f"manifest only accepts JSON basic types, got {type(value).__name__}")

    def build(self) -> dict[str, Any]:
        manifest = {
            "analyzer": self.analyzer,
            "version": __version__,
            "status": self.status,
            "error": self.error,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "options": self.options,
            "inputs": self._build_inputs(),
            "output": self.output,
        }
        self._ensure_json_basic(manifest)
        return manifest

    def write(self) -> None:
        Ns_IO.dump_json_atomic(self.build(), self.manifest_path)
