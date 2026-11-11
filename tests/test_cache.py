#!/usr/bin/env python3

import io
import json
import os
import os.path as os_path
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from neosca.ns_io import Ns_Cache
from neosca.ns_main_cli import Ns_Main_Cli, main_cli

from .base_tmpl import BaseTmpl


class CacheTmpl(BaseTmpl):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.cache_dir = self.root / "cache"
        self.cache_dir.mkdir()
        self.info_path = self.root / "cache_info.json"
        self.inputs_dir = self.root / "inputs"
        self.inputs_dir.mkdir()

        self._patches = (
            mock.patch("neosca.ns_io.CACHE_DIR", self.cache_dir),
            mock.patch("neosca.ns_io.CACHE_INFO_PATH", self.info_path),
        )
        for patch in self._patches:
            patch.start()

        Ns_Cache.fpath_cname = {}
        Ns_Cache.info_changed = False
        self.cli = Ns_Main_Cli()
        self.base_time = time.time()

    def tearDown(self):
        for patch in self._patches:
            patch.stop()
        self._tmp.cleanup()
        super().tearDown()

    def make_input(self, name: str, content: str = "input") -> Path:
        path = self.inputs_dir / name
        path.write_text(content)
        return path

    def make_cache(self, name: str, content: str = "cached") -> Path:
        path = self.cache_dir / name
        path.write_text(content)
        return path

    def register(self, input_path: Path, cache_name: str) -> None:
        Ns_Cache.fpath_cname[str(input_path)] = cache_name

    def set_mtime(self, path: Path, offset_seconds: float) -> None:
        ts = self.base_time + offset_seconds
        os.utime(path, (ts, ts))

    def make_valid(self, stem: str = "valid") -> tuple[Path, Path]:
        input_path = self.make_input(f"{stem}.txt")
        cache_path = self.make_cache(f"{stem}.pickle.lzma")
        self.set_mtime(input_path, -100)
        self.set_mtime(cache_path, 0)
        self.register(input_path, cache_path.name)
        return input_path, cache_path

    def make_outdated(self, stem: str = "outdated") -> tuple[Path, Path]:
        input_path = self.make_input(f"{stem}.txt")
        cache_path = self.make_cache(f"{stem}.pickle.lzma")
        self.set_mtime(cache_path, -100)
        self.set_mtime(input_path, 0)
        self.register(input_path, cache_path.name)
        return input_path, cache_path

    def make_missing(self, stem: str = "missing") -> tuple[Path, str]:
        input_path = self.make_input(f"{stem}.txt")
        cache_name = f"{stem}.pickle.lzma"
        self.register(input_path, cache_name)
        return input_path, cache_name

    def make_orphaned(self, stem: str = "orphaned") -> tuple[Path, Path]:
        input_path = self.inputs_dir / f"{stem}.txt"  # intentionally not created
        cache_path = self.make_cache(f"{stem}.pickle.lzma")
        self.register(input_path, cache_path.name)
        return input_path, cache_path

    def make_all_four_states(self) -> dict[str, tuple]:
        return {
            Ns_Cache.STATUS_VALID: self.make_valid("a_valid"),
            Ns_Cache.STATUS_OUTDATED: self.make_outdated("b_outdated"),
            Ns_Cache.STATUS_MISSING: self.make_missing("c_missing"),
            Ns_Cache.STATUS_ORPHANED: self.make_orphaned("d_orphaned"),
        }

    def status_map(self) -> dict[str, dict]:
        return {entry["status"]: entry for entry in Ns_Cache.list_entries()}

    def run_cli(self, argv: list[str]) -> tuple[bool, str | None, str]:
        success, err_msg = self.cli.parse_args(argv)
        self.assertTrue(success)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            success, err_msg = self.cli.run()
        return success, err_msg, buffer.getvalue()


class TestCacheInspection(CacheTmpl):
    def test_four_statuses(self):
        made = self.make_all_four_states()
        entries = Ns_Cache.list_entries()
        self.assertEqual(len(entries), 4)
        by_status = {entry["status"]: entry for entry in entries}
        self.assertEqual(set(by_status), set(Ns_Cache.ALL_STATUSES))

        valid_input, valid_cache = made[Ns_Cache.STATUS_VALID]
        valid = by_status[Ns_Cache.STATUS_VALID]
        self.assertTrue(valid["cache_exists"])
        self.assertTrue(valid["input_exists"])
        self.assertFalse(valid["is_outdated"])
        self.assertFalse(valid["is_orphaned"])
        self.assertTrue(valid["is_regular_file"])
        self.assertFalse(valid["is_symlink"])
        self.assertEqual(valid["cache_size"], len("cached"))
        self.assertIsInstance(valid["cache_mtime_epoch"], float)
        self.assertIn("T", valid["cache_mtime"])
        self.assertEqual(valid["input_path"], str(valid_input))
        self.assertEqual(valid["cache_path"], str(valid_cache))

        missing = by_status[Ns_Cache.STATUS_MISSING]
        self.assertFalse(missing["cache_exists"])
        self.assertTrue(missing["input_exists"])
        self.assertIsNone(missing["cache_size"])
        self.assertIsNone(missing["cache_mtime"])
        self.assertIsNone(missing["cache_mtime_epoch"])

        outdated = by_status[Ns_Cache.STATUS_OUTDATED]
        self.assertTrue(outdated["is_outdated"])
        self.assertFalse(outdated["is_orphaned"])

        orphaned = by_status[Ns_Cache.STATUS_ORPHANED]
        self.assertFalse(orphaned["input_exists"])
        self.assertTrue(orphaned["is_orphaned"])
        self.assertTrue(orphaned["cache_exists"])

    def test_empty_cache_file_is_outdated(self):
        input_path = self.make_input("empty.txt")
        cache_path = self.cache_dir / "empty.pickle.lzma"
        cache_path.write_text("")
        self.set_mtime(input_path, -100)
        self.set_mtime(cache_path, 0)
        self.register(input_path, cache_path.name)

        entry = Ns_Cache.inspect_entry(str(input_path), cache_path.name)
        self.assertEqual(entry["status"], Ns_Cache.STATUS_OUTDATED)
        self.assertTrue(entry["is_outdated"])
        self.assertEqual(entry["cache_size"], 0)

    def test_list_sorted_by_input_path(self):
        self.make_orphaned("z_orphan")
        self.make_valid("a_valid")
        self.make_missing("m_missing")
        input_paths = [entry["input_path"] for entry in Ns_Cache.list_entries()]
        self.assertEqual(input_paths, sorted(input_paths))

    def test_empty_cache(self):
        self.assertEqual(Ns_Cache.list_entries(), [])


class TestCachePrune(CacheTmpl):
    def test_dry_run_changes_nothing(self):
        made = self.make_all_four_states()
        report = Ns_Cache.prune_entries(Ns_Cache.PRUNABLE_STATUSES, apply=False)

        self.assertFalse(report["apply"])
        self.assertFalse(report["metadata_updated"])
        self.assertEqual(report["summary"]["selected"], 3)
        self.assertEqual(report["summary"]["would_delete"], 3)
        self.assertEqual(report["summary"]["deleted"], 0)
        self.assertEqual(report["summary"]["failed"], 0)
        self.assertTrue(all(entry["action"] == "would_delete" for entry in report["entries"]))

        # Nothing on disk changed and no metadata file was created.
        self.assertFileNotExist(str(self.info_path))
        for status, item in made.items():
            if status == Ns_Cache.STATUS_MISSING:
                continue
            self.assertFileExists(str(item[1]))
        self.assertEqual(len(Ns_Cache.fpath_cname), 4)

    def test_apply_removes_selected_and_updates_metadata_atomically(self):
        made = self.make_all_four_states()
        report = Ns_Cache.prune_entries(Ns_Cache.PRUNABLE_STATUSES, apply=True)

        self.assertTrue(report["metadata_updated"])
        self.assertEqual(report["summary"]["failed"], 0)
        self.assertEqual(report["summary"]["deleted"], 3)

        # Valid cache survives; the other on-disk caches are gone.
        self.assertFileExists(str(made[Ns_Cache.STATUS_VALID][1]))
        self.assertFileNotExist(str(made[Ns_Cache.STATUS_OUTDATED][1]))
        self.assertFileNotExist(str(made[Ns_Cache.STATUS_ORPHANED][1]))

        # Source/input files are never touched.
        for status in (Ns_Cache.STATUS_VALID, Ns_Cache.STATUS_OUTDATED, Ns_Cache.STATUS_MISSING):
            self.assertFileExists(str(made[status][0]))

        # cache_info is rewritten and only keeps the valid entry.
        self.assertFileExists(str(self.info_path))
        on_disk = json.loads(self.info_path.read_text())
        self.assertEqual(on_disk, {str(made[Ns_Cache.STATUS_VALID][0]): "a_valid.pickle.lzma"})
        self.assertEqual(Ns_Cache.fpath_cname, on_disk)
        # No leftover temp files from the atomic write.
        self.assertEqual(
            [name for name in os.listdir(self.root) if name.startswith(".cache_info.json")],
            [],
        )

    def test_missing_selection_only_drops_metadata(self):
        made = self.make_all_four_states()
        report = Ns_Cache.prune_entries((Ns_Cache.STATUS_MISSING,), apply=True)

        self.assertEqual(report["summary"]["deleted"], 1)
        self.assertEqual(report["summary"]["failed"], 0)
        on_disk = json.loads(self.info_path.read_text())
        missing_input = str(made[Ns_Cache.STATUS_MISSING][0])
        self.assertNotIn(missing_input, on_disk)
        self.assertEqual(len(on_disk), 3)
        # All actual cache files remain untouched.
        for status, item in made.items():
            if status == Ns_Cache.STATUS_MISSING:
                continue
            self.assertFileExists(str(item[1]))

    def test_mixed_selection(self):
        made = self.make_all_four_states()
        report = Ns_Cache.prune_entries((Ns_Cache.STATUS_MISSING, Ns_Cache.STATUS_OUTDATED), apply=True)

        self.assertEqual(report["summary"]["selected"], 2)
        self.assertEqual(report["summary"]["deleted"], 2)
        self.assertEqual(report["summary"]["failed"], 0)
        self.assertFileNotExist(str(made[Ns_Cache.STATUS_OUTDATED][1]))
        # Orphaned and valid entries are untouched.
        self.assertFileExists(str(made[Ns_Cache.STATUS_ORPHANED][1]))
        self.assertFileExists(str(made[Ns_Cache.STATUS_VALID][1]))
        on_disk = json.loads(self.info_path.read_text())
        self.assertEqual(len(on_disk), 2)
        self.assertIn(str(made[Ns_Cache.STATUS_ORPHANED][0]), on_disk)
        self.assertIn(str(made[Ns_Cache.STATUS_VALID][0]), on_disk)

    def test_apply_empty_cache_writes_nothing(self):
        report = Ns_Cache.prune_entries(Ns_Cache.PRUNABLE_STATUSES, apply=True)
        self.assertEqual(report["summary"]["selected"], 0)
        self.assertFalse(report["metadata_updated"])
        self.assertFileNotExist(str(self.info_path))

    def test_statuses_deduplicated(self):
        self.make_missing()
        report = Ns_Cache.prune_entries((Ns_Cache.STATUS_MISSING, Ns_Cache.STATUS_MISSING), apply=False)
        self.assertEqual(report["statuses"], [Ns_Cache.STATUS_MISSING])
        self.assertEqual(report["summary"]["selected"], 1)

    def test_symlink_is_refused_and_target_kept(self):
        target = self.root / "secret.txt"
        target.write_text("do not delete")
        link = self.cache_dir / "link.pickle.lzma"
        link.symlink_to(target)
        gone_input = self.inputs_dir / "gone.txt"
        self.register(gone_input, link.name)

        entry = Ns_Cache.inspect_entry(str(gone_input), link.name)
        self.assertEqual(entry["status"], Ns_Cache.STATUS_ORPHANED)
        self.assertTrue(entry["is_symlink"])
        self.assertFalse(entry["is_regular_file"])

        report = Ns_Cache.prune_entries((Ns_Cache.STATUS_ORPHANED,), apply=True)
        self.assertEqual(report["summary"]["failed"], 1)
        self.assertEqual(report["summary"]["deleted"], 0)
        # The link itself and its target are both still there.
        self.assertTrue(os_path.lexists(link))
        self.assertFileExists(str(target))
        # Metadata of the failed entry is retained.
        self.assertIn(str(gone_input), Ns_Cache.fpath_cname)
        self.assertFileNotExist(str(self.info_path))
        self.assertIn("symbolic link", report["entries"][0]["reason"])

    def test_non_regular_file_is_refused(self):
        subdir = self.cache_dir / "subdir.pickle.lzma"
        subdir.mkdir()
        gone_input = self.inputs_dir / "gone.txt"
        self.register(gone_input, subdir.name)

        report = Ns_Cache.prune_entries(Ns_Cache.PRUNABLE_STATUSES, apply=True)
        self.assertEqual(report["summary"]["failed"], 1)
        self.assertTrue(os_path.isdir(subdir))
        self.assertIn(str(gone_input), Ns_Cache.fpath_cname)

    def test_root_escape_with_parent_directory(self):
        escaped = self.root / "escape.pickle.lzma"
        escaped.write_text("outside cache root")
        gone_input = self.inputs_dir / "gone.txt"
        Ns_Cache.fpath_cname[str(gone_input)] = "../escape.pickle.lzma"

        report = Ns_Cache.prune_entries(Ns_Cache.PRUNABLE_STATUSES, apply=True)
        self.assertEqual(report["summary"]["failed"], 1)
        self.assertFileExists(str(escaped))
        self.assertIn(str(gone_input), Ns_Cache.fpath_cname)

    def test_root_escape_with_absolute_path(self):
        evil = self.root / "evil.pickle.lzma"
        evil.write_text("absolute path")
        gone_input = self.inputs_dir / "gone.txt"
        Ns_Cache.fpath_cname[str(gone_input)] = str(evil)

        report = Ns_Cache.prune_entries(Ns_Cache.PRUNABLE_STATUSES, apply=True)
        self.assertEqual(report["summary"]["failed"], 1)
        self.assertFileExists(str(evil))
        self.assertIn(str(gone_input), Ns_Cache.fpath_cname)

    def test_partial_delete_failure_keeps_failed_metadata(self):
        first_input, first_cache = self.make_outdated("first")
        second_input, second_cache = self.make_outdated("second")
        original_mapping = dict(Ns_Cache.fpath_cname)

        real_remove = os.remove

        def selective_remove(path, *args, **kwargs):
            if os_path.abspath(path) == os_path.abspath(first_cache):
                raise PermissionError("simulated failure")
            real_remove(path)

        with mock.patch("neosca.ns_io.os.remove", side_effect=selective_remove):
            report = Ns_Cache.prune_entries((Ns_Cache.STATUS_OUTDATED,), apply=True)

        self.assertEqual(report["summary"]["deleted"], 1)
        self.assertEqual(report["summary"]["failed"], 1)
        self.assertTrue(report["metadata_updated"])
        # Failed cache file and its metadata remain; the other one is gone.
        self.assertFileExists(str(first_cache))
        self.assertFileNotExist(str(second_cache))
        on_disk = json.loads(self.info_path.read_text())
        self.assertIn(str(first_input), on_disk)
        self.assertNotIn(str(second_input), on_disk)
        self.assertEqual(Ns_Cache.fpath_cname, on_disk)
        self.assertEqual(Ns_Cache.fpath_cname[str(first_input)], original_mapping[str(first_input)])
        # Both source files remain.
        self.assertFileExists(str(first_input))
        self.assertFileExists(str(second_input))

    def test_atomic_write_failure_keeps_original_metadata(self):
        _, outdated_cache = self.make_outdated()
        original_mapping = dict(Ns_Cache.fpath_cname)
        Ns_Cache._dump_cache_info_atomic(original_mapping)
        original_content = self.info_path.read_text()

        with mock.patch("neosca.ns_io.os.replace", side_effect=OSError("simulated replace failure")):
            report = Ns_Cache.prune_entries((Ns_Cache.STATUS_OUTDATED,), apply=True)

        self.assertEqual(report["summary"]["failed"], 1)
        self.assertFalse(report["metadata_updated"])
        # The old cache_info stays intact, in memory and on disk.
        self.assertEqual(self.info_path.read_text(), original_content)
        self.assertEqual(Ns_Cache.fpath_cname, original_mapping)
        # The cache file itself was already deleted, but no temp file is left.
        self.assertFileNotExist(str(outdated_cache))
        self.assertEqual(
            [name for name in os.listdir(self.root) if name.endswith(".tmp")],
            [],
        )

    def test_atomic_write_uses_sorted_json_keys(self):
        self.make_valid()
        Ns_Cache._dump_cache_info_atomic(dict(Ns_Cache.fpath_cname))
        content = self.info_path.read_text()
        parsed = json.loads(content)
        self.assertEqual(parsed, Ns_Cache.fpath_cname)
        # Re-serialization with the same settings must be byte-identical.
        self.assertEqual(
            content,
            json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )


class TestCacheCli(CacheTmpl):
    def test_list_text_output(self):
        self.make_all_four_states()
        success, err_msg, output = self.run_cli(["nsca", "cache", "list"])
        self.assertTrue(success, err_msg)
        for header in (
            "INPUT PATH",
            "CACHE PATH",
            "SIZE (BYTES)",
            "MODIFIED (UTC)",
            "CACHE EXISTS",
            "INPUT EXISTS",
            "OUTDATED",
            "ORPHANED",
            "STATUS",
        ):
            self.assertIn(header, output)
        for status in Ns_Cache.ALL_STATUSES:
            self.assertIn(status, output)
        self.assertIn("4 entries: 1 valid, 1 missing, 1 outdated, 1 orphaned.", output)
        self.assertIn(str(self.cache_dir), output)

    def test_list_empty_text_output(self):
        _, _, output = self.run_cli(["nsca", "cache", "list"])
        self.assertIn("No cache entries found.", output)

    def test_prune_empty_text_output(self):
        _, _, output = self.run_cli(["nsca", "cache", "prune"])
        self.assertIn("Mode: dry run", output)
        self.assertIn("No cache entries matched the selected statuses.", output)

    def test_list_json_sorting_and_fields(self):
        self.make_orphaned("z_orphan")
        self.make_valid("a_valid")
        _, _, output = self.run_cli(["nsca", "cache", "list", "--format", "json"])
        payload = json.loads(output)
        self.assertEqual(payload["count"], 2)
        input_paths = [entry["input_path"] for entry in payload["entries"]]
        self.assertEqual(input_paths, sorted(input_paths))
        for entry in payload["entries"]:
            self.assertEqual(list(entry.keys()), sorted(entry.keys()))
            for field in (
                "input_path",
                "cache_path",
                "cache_name",
                "cache_size",
                "cache_mtime",
                "cache_mtime_epoch",
                "cache_exists",
                "input_exists",
                "is_outdated",
                "is_orphaned",
                "is_symlink",
                "is_regular_file",
                "status",
            ):
                self.assertIn(field, entry)
        self.assertEqual(list(payload.keys()), sorted(payload.keys()))

    def test_list_json_to_explicit_output_file(self):
        self.make_valid()
        out_file = self.root / "reports" / "cache_list.json"
        success, err_msg, output = self.run_cli(
            ["nsca", "cache", "list", "--format", "json", "-o", str(out_file)]
        )
        self.assertTrue(success, err_msg)
        self.assertEqual(output, "")
        self.assertFileExists(str(out_file))
        payload = json.loads(out_file.read_text())
        self.assertEqual(payload["count"], 1)

    def test_prune_dry_run_text(self):
        self.make_outdated()
        success, err_msg, output = self.run_cli(["nsca", "cache", "prune"])
        self.assertTrue(success, err_msg)
        self.assertIn("Mode: dry run", output)
        self.assertIn("would delete", output)
        self.assertIn("Cache info updated: no", output)
        self.assertEqual(len(Ns_Cache.fpath_cname), 1)
        self.assertFileNotExist(str(self.info_path))

    def test_prune_apply_text(self):
        _, cache_path = self.make_outdated()
        success, err_msg, output = self.run_cli(["nsca", "cache", "prune", "--status", "outdated", "--apply"])
        self.assertTrue(success, err_msg)
        self.assertIn("Mode: apply", output)
        self.assertIn("deleted", output)
        self.assertIn("Cache info updated: yes", output)
        self.assertFileNotExist(str(cache_path))
        self.assertFileExists(str(self.info_path))

    def test_prune_json_output(self):
        self.make_missing()
        success, err_msg, output = self.run_cli(["nsca", "cache", "prune", "--format", "json", "--apply"])
        self.assertTrue(success, err_msg)
        payload = json.loads(output)
        self.assertTrue(payload["apply"])
        self.assertEqual(payload["summary"]["deleted"], 1)
        self.assertEqual(list(payload.keys()), sorted(payload.keys()))
        self.assertEqual(list(payload["entries"][0].keys()), sorted(payload["entries"][0].keys()))

    def test_parser_defaults_and_selection(self):
        self.cli.parse_args(["nsca", "cache", "prune"])
        self.assertEqual(self.cli.options.statuses, list(Ns_Cache.PRUNABLE_STATUSES))
        self.assertFalse(self.cli.options.is_apply)
        self.assertEqual(self.cli.options.oformat, "text")
        self.assertIsNone(self.cli.options.ofile)

        self.cli.parse_args(["nsca", "cache", "prune", "--status", "missing", "outdated", "--apply"])
        self.assertEqual(self.cli.options.statuses, ["missing", "outdated"])
        self.assertTrue(self.cli.options.is_apply)

        self.cli.parse_args(["nsca", "cache", "prune", "--status", "missing", "missing", "orphaned"])
        self.assertEqual(self.cli.options.statuses, ["missing", "orphaned"])

    def test_invalid_status_rejected(self):
        with self.assertRaises(SystemExit):
            self.cli.parse_args(["nsca", "cache", "prune", "--status", "bogus"])

    def test_cache_without_subcommand_prints_help(self):
        success, err_msg, _ = self.run_cli(["nsca", "cache"])
        self.assertTrue(success, err_msg)

    def test_prune_failure_returns_nonzero(self):
        target = self.root / "secret.txt"
        target.write_text("keep me")
        link = self.cache_dir / "link.pickle.lzma"
        link.symlink_to(target)
        gone_input = self.inputs_dir / "gone.txt"
        self.register(gone_input, link.name)

        success, err_msg, output = self.run_cli(["nsca", "cache", "prune", "--apply", "--format", "json"])
        self.assertFalse(success)
        self.assertIsNotNone(err_msg)
        payload = json.loads(output)
        self.assertEqual(payload["summary"]["failed"], 1)
        self.assertTrue(os_path.lexists(link))
        self.assertFileExists(str(target))

    def test_main_cli_exits_nonzero_on_prune_failure(self):
        target = self.root / "secret.txt"
        target.write_text("keep me")
        link = self.cache_dir / "link.pickle.lzma"
        link.symlink_to(target)
        gone_input = self.inputs_dir / "gone.txt"
        self.register(gone_input, link.name)

        with (
            mock.patch("sys.argv", ["nsca", "cache", "prune", "--apply"]),
            mock.patch("sys.exit", side_effect=SystemExit(1)) as mock_exit,
            self.assertRaises(SystemExit),
        ):
            main_cli()
        mock_exit.assert_called_once_with(1)
        self.assertTrue(os_path.lexists(link))
        self.assertFileExists(str(target))
