#!/usr/bin/env python3

import json
import lzma
import os
import os.path as os_path
import subprocess
import sys
import tempfile
import types
from datetime import datetime
from pathlib import Path
from unittest import mock

from neosca.ns_about import __version__
from neosca.ns_io import Ns_Cache, Ns_IO
from neosca.ns_main_cli import Ns_Main_Cli
from neosca.ns_manifest import Ns_Manifest

from . import SRC_DIR
from .base_tmpl import BaseTmpl, tree

TREE_TEXT = tree
MARKER_TREE = "(ROOT (S (NP (DT this)) (VP (VBZ zzqmarkerzz_runs)) (. .)))"
MARKER_TEXT = "the distinct zzqmarkerzz sentence for manifest hashing only"
CANNED_LEMPOS = (
    ("there", "EX"),
    ("be", "VBD"),
    ("no", "DT"),
    ("possibility", "NN"),
    ("of", "IN"),
    ("take", "VBG"),
    ("a", "DT"),
    ("walk", "NN"),
    ("that", "DT"),
    ("day", "NN"),
)
TOP_LEVEL_KEYS = [
    "analyzer",
    "version",
    "status",
    "error",
    "start_time",
    "end_time",
    "options",
    "inputs",
    "output",
]
FILE_RECORD_KEYS = ["kind", "path", "size", "sha256", "cache_path", "cache_hit"]
TEXT_RECORD_KEYS = ["kind", "identifier", "sha256"]


class ManifestTestMixin:
    def run_cli(self, argv: list[str]) -> tuple[Ns_Main_Cli, bool, str | None]:
        ui = Ns_Main_Cli()
        success, err_msg = ui.parse_args(argv)
        self.assertTrue(success, msg=err_msg)
        return ui, *ui.run()

    @staticmethod
    def load_manifest(manifest_path: str) -> dict:
        with open(manifest_path, encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def write_file(directory: str, name: str, content: str) -> str:
        path = os_path.join(directory, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    @staticmethod
    def without_timestamps(manifest: dict) -> dict:
        stripped = dict(manifest)
        stripped["start_time"] = "<timestamp>"
        stripped["end_time"] = "<timestamp>"
        return stripped


class ManifestBaseTmpl(BaseTmpl):
    """Installs an in-process stub for the optional stanza dependency."""

    def setUp(self):
        super().setUp()
        self.stanza_stub = None
        try:
            import stanza  # noqa: F401
        except ImportError:
            self.stanza_stub = types.ModuleType("stanza")

            class _Document:
                pass

            self.stanza_stub.Document = _Document
            sys.modules["stanza"] = self.stanza_stub
        self.addCleanup(self._remove_stanza_stub)

    def _remove_stanza_stub(self) -> None:
        if self.stanza_stub is not None:
            sys.modules.pop("stanza", None)


class TestManifestPrimitives(BaseTmpl, ManifestTestMixin):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_sha256_helpers(self):
        self.assertEqual(
            Ns_IO.sha256_bytes(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        file_path = self.write_file(self.tmp.name, "sample.txt", TREE_TEXT)
        with open(file_path, "rb") as f:
            expected = Ns_IO.sha256_bytes(f.read())
        self.assertEqual(Ns_IO.sha256_file(file_path), expected)

    def test_ensure_json_basic_rejects_other_types(self):
        Ns_Manifest._ensure_json_basic({"a": [1, 2.0, True, False, None, "s", {"b": 3}]})
        with self.assertRaises(TypeError):
            Ns_Manifest._ensure_json_basic({"a": {1, 2}})
        with self.assertRaises(TypeError):
            Ns_Manifest._ensure_json_basic({"a": datetime.now()})
        with self.assertRaises(TypeError):
            Ns_Manifest._ensure_json_basic({1: "x"})

    def test_atomic_write_roundtrip_and_no_temp_left(self):
        target = os_path.join(self.tmp.name, "manifest.json")
        payload = {"status": "success", "inputs": [{"path": "a.txt"}]}
        Ns_IO.dump_json_atomic(payload, target)

        self.assertTrue(os_path.isfile(target))
        self.assertEqual(self.load_manifest(target), payload)
        leftovers = [name for name in os.listdir(self.tmp.name) if name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_atomic_write_creates_parent_directory(self):
        target = os_path.join(self.tmp.name, "nested", "deep", "manifest.json")
        self.assertFalse(os_path.exists(os_path.dirname(target)))
        Ns_IO.dump_json_atomic({"ok": True}, target)
        self.assertTrue(os_path.isfile(target))

    def test_atomic_write_serialization_failure_touches_nothing(self):
        target = os_path.join(self.tmp.name, "manifest.json")
        with self.assertRaises(TypeError):
            Ns_IO.dump_json_atomic({"bad": {1, 2}}, target)
        self.assertFalse(os_path.exists(target))
        self.assertEqual(os.listdir(self.tmp.name), [])

    def test_atomic_write_replace_failure_leaves_no_half_file(self):
        target = os_path.join(self.tmp.name, "manifest.json")
        with (
            mock.patch("neosca.ns_io.os.replace", side_effect=OSError("boom")),
            self.assertRaises(OSError),
        ):
            Ns_IO.dump_json_atomic({"ok": True}, target)
        self.assertFalse(os_path.exists(target))
        leftovers = [name for name in os.listdir(self.tmp.name) if name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_manifest_field_order_and_sorting(self):
        manifest = Ns_Manifest("sca", os_path.join(self.tmp.name, "m.json"), {"b": 1, "a": 2})
        manifest.start()
        for name in ("z.txt", "a.txt", "m.txt"):
            path = self.write_file(self.tmp.name, name, TREE_TEXT)
            manifest.record_file(path)
        manifest.record_text(MARKER_TEXT)
        manifest.mark_failure(ValueError("boom"))

        built = manifest.build()
        self.assertEqual(list(built.keys()), TOP_LEVEL_KEYS)
        self.assertEqual(list(built["options"].keys()), ["a", "b"])

        inputs = built["inputs"]
        file_paths = [record["path"] for record in inputs[:-1]]
        self.assertEqual(file_paths, sorted(file_paths))
        self.assertEqual(inputs[-1]["kind"], "text")
        self.assertEqual(inputs[-1]["identifier"], Ns_Manifest.TEXT_IDENTIFIER)


class TestScaManifest(ManifestBaseTmpl, ManifestTestMixin):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.input_a = self.write_file(self.tmp.name, "a.txt", TREE_TEXT)
        self.input_b = self.write_file(self.tmp.name, "b.txt", TREE_TEXT)
        self.output_csv = os_path.join(self.tmp.name, "out.csv")
        self.output_json = os_path.join(self.tmp.name, "out.json")
        self.manifest_path = os_path.join(self.tmp.name, "run.manifest.json")

    def _base_args(self, *extra: str) -> list[str]:
        return ["nsca", "sca", "--no-parse", self.input_a, self.input_b, *extra]

    def _assert_file_record(self, record: dict, file_path: str) -> None:
        self.assertEqual(list(record.keys()), FILE_RECORD_KEYS)
        self.assertEqual(record["kind"], "file")
        self.assertEqual(record["path"], os_path.abspath(file_path))
        self.assertEqual(record["size"], os_path.getsize(file_path))
        self.assertEqual(record["sha256"], Ns_IO.sha256_file(file_path))
        self.assertIsInstance(record["cache_hit"], bool)

    def test_sca_file_input_csv_manifest(self):
        _, success, err_msg = self.run_cli(
            self._base_args("-o", self.output_csv, "--manifest", self.manifest_path)
        )
        self.assertTrue(success, msg=err_msg)

        manifest = self.load_manifest(self.manifest_path)
        self.assertEqual(list(manifest.keys()), TOP_LEVEL_KEYS)
        self.assertEqual(manifest["analyzer"], "sca")
        self.assertEqual(manifest["version"], __version__)
        self.assertEqual(manifest["status"], "success")
        self.assertIsNone(manifest["error"])
        self.assertIsInstance(manifest["start_time"], str)
        self.assertIsInstance(manifest["end_time"], str)
        self.assertLessEqual(manifest["start_time"], manifest["end_time"])

        options = manifest["options"]
        self.assertEqual(list(options.keys()), sorted(options.keys()))
        self.assertTrue(options["no_parse"])
        self.assertFalse(options["cache"])
        self.assertFalse(options["use_cache"])
        self.assertFalse(options["text"])
        self.assertFalse(options["stdout"])
        self.assertIsNone(options["select"])
        self.assertEqual(options["output_format"], "csv")
        self.assertEqual(options["output_file"], os_path.abspath(self.output_csv))
        self.assertEqual(options["manifest"], os_path.abspath(self.manifest_path))
        self.assertEqual(options["combine_files"], [])
        self.assertEqual(options["ftype"], sorted(options["ftype"]))

        inputs = manifest["inputs"]
        self.assertEqual(len(inputs), 2)
        self.assertEqual([record["path"] for record in inputs], sorted(record["path"] for record in inputs))
        self._assert_file_record(inputs[0], self.input_a)
        self._assert_file_record(inputs[1], self.input_b)
        for record in inputs:
            self.assertIsNone(record["cache_path"])
            self.assertFalse(record["cache_hit"])

        self.assertEqual(manifest["output"]["path"], os_path.abspath(self.output_csv))
        self.assertEqual(manifest["output"]["sha256"], Ns_IO.sha256_file(self.output_csv))
        self.assertEqual(set(manifest["output"].keys()), {"path", "sha256"})

    def test_sca_file_input_json_manifest(self):
        _, success, err_msg = self.run_cli(
            self._base_args("-o", self.output_json, "--manifest", self.manifest_path)
        )
        self.assertTrue(success, msg=err_msg)

        # The result file itself is valid JSON
        with open(self.output_json, encoding="utf-8") as f:
            json.load(f)

        manifest = self.load_manifest(self.manifest_path)
        self.assertEqual(manifest["options"]["output_format"], "json")
        self.assertEqual(manifest["output"]["sha256"], Ns_IO.sha256_file(self.output_json))

    def test_sca_text_input_keeps_only_identifier_and_hash(self):
        _, success, err_msg = self.run_cli(
            [
                "nsca",
                "sca",
                "--no-parse",
                "--text",
                MARKER_TREE,
                "-o",
                self.output_csv,
                "--manifest",
                self.manifest_path,
            ]
        )
        self.assertTrue(success, msg=err_msg)

        manifest = self.load_manifest(self.manifest_path)
        self.assertTrue(manifest["options"]["text"])
        self.assertEqual(len(manifest["inputs"]), 1)
        text_record = manifest["inputs"][0]
        self.assertEqual(list(text_record.keys()), TEXT_RECORD_KEYS)
        self.assertEqual(text_record["kind"], "text")
        self.assertEqual(text_record["identifier"], "cli_text")
        self.assertEqual(text_record["sha256"], Ns_IO.sha256_bytes(MARKER_TREE.encode("utf-8")))

        with open(self.manifest_path, "rb") as f:
            raw_manifest = f.read()
        self.assertNotIn(b"zzqmarkerzz", raw_manifest)

    def test_sca_combine_subfiles_recorded(self):
        _, success, err_msg = self.run_cli(
            [
                "nsca",
                "sca",
                "--no-parse",
                "-c",
                self.input_b,
                self.input_a,
                "-o",
                self.output_csv,
                "--manifest",
                self.manifest_path,
            ]
        )
        self.assertTrue(success, msg=err_msg)

        manifest = self.load_manifest(self.manifest_path)
        input_paths = [record["path"] for record in manifest["inputs"]]
        self.assertEqual(
            input_paths,
            sorted([os_path.abspath(self.input_a), os_path.abspath(self.input_b)]),
        )
        self.assertEqual(
            manifest["options"]["combine_files"],
            [[os_path.abspath(self.input_a), os_path.abspath(self.input_b)]],
        )

    def test_sca_inputs_are_path_sorted_with_text_last(self):
        names = ("z.txt", "a.txt", "m.txt")
        paths = [self.write_file(self.tmp.name, n, TREE_TEXT) for n in names]
        _, success, err_msg = self.run_cli(
            [
                "nsca",
                "sca",
                "--no-parse",
                *paths,
                "--text",
                MARKER_TREE,
                "-o",
                self.output_csv,
                "--manifest",
                self.manifest_path,
            ]
        )
        self.assertTrue(success, msg=err_msg)

        manifest = self.load_manifest(self.manifest_path)
        inputs = manifest["inputs"]
        file_paths = [record["path"] for record in inputs[:-1]]
        self.assertEqual(file_paths, sorted(file_paths))
        self.assertEqual(inputs[-1]["kind"], "text")

    def test_sca_repeated_runs_are_identical_except_timestamps(self):
        argv = self._base_args("-o", self.output_csv, "--manifest", self.manifest_path)
        _, success, first_err = self.run_cli(argv)
        self.assertTrue(success, msg=first_err)
        first = self.without_timestamps(self.load_manifest(self.manifest_path))

        _, success, second_err = self.run_cli(argv)
        self.assertTrue(success, msg=second_err)
        second = self.without_timestamps(self.load_manifest(self.manifest_path))

        self.assertEqual(first, second)

    def test_sca_failure_writes_failure_manifest_without_faking_success(self):
        missing_output = os_path.join(self.tmp.name, "no_such_dir", "out.csv")
        _, success, err_msg = self.run_cli(
            self._base_args("-o", missing_output, "--manifest", self.manifest_path)
        )
        self.assertFalse(success)
        self.assertIn("FileNotFoundError", err_msg)

        manifest = self.load_manifest(self.manifest_path)
        self.assertEqual(manifest["status"], "failure")
        self.assertEqual(manifest["error"]["type"], "FileNotFoundError")
        self.assertIsInstance(manifest["error"]["message"], str)
        self.assertIsNone(manifest["output"])
        self.assertIsNotNone(manifest["end_time"])
        self.assertFalse(os_path.exists(missing_output))
        leftovers = [
            name
            for name in os.listdir(self.tmp.name)
            if name.endswith(".tmp") or name.startswith(".run.manifest.json")
        ]
        self.assertEqual(leftovers, [])

    def test_sca_failure_without_manifest_keeps_raising(self):
        missing_output = os_path.join(self.tmp.name, "no_such_dir", "out.csv")
        ui = Ns_Main_Cli()
        success, err_msg = ui.parse_args(self._base_args("-o", missing_output))
        self.assertTrue(success, msg=err_msg)
        with self.assertRaises(OSError):
            ui.run()
        self.assertFalse(os_path.exists(self.manifest_path))


class TestLcaManifest(ManifestBaseTmpl, ManifestTestMixin):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        from neosca.ns_nlp import Ns_NLP_Stanza

        self.Ns_NLP_Stanza = Ns_NLP_Stanza
        self.lempos_patcher = mock.patch.object(Ns_NLP_Stanza, "get_lemma_and_pos", return_value=CANNED_LEMPOS)
        self.serialized_patcher = mock.patch.object(Ns_NLP_Stanza, "serialized2doc", return_value=object())
        self.lempos_patcher.start()
        self.serialized_patcher.start()
        self.addCleanup(self.lempos_patcher.stop)
        self.addCleanup(self.serialized_patcher.stop)

        # Redirect all cache artifacts into the temporary directory.
        self.cache_dir = Path(self.tmp.name) / "cache"
        self.cache_dir.mkdir()
        self.cache_info_path = self.cache_dir / "cache_info.json"
        self.cache_patches = [
            mock.patch("neosca.ns_io.CACHE_DIR", self.cache_dir),
            mock.patch("neosca.ns_consts.CACHE_DIR", self.cache_dir),
            mock.patch("neosca.ns_io.CACHE_INFO_PATH", self.cache_info_path),
        ]
        for patcher in self.cache_patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self._original_fpath_cname = dict(Ns_Cache.fpath_cname)
        self._original_info_changed = Ns_Cache.info_changed
        Ns_Cache.fpath_cname = {}
        Ns_Cache.info_changed = False
        self.addCleanup(self._restore_cache_state)

        self.input_a = self.write_file(self.tmp.name, "a.txt", MARKER_TEXT)
        self.input_b = self.write_file(self.tmp.name, "b.txt", MARKER_TEXT)
        self.output_csv = os_path.join(self.tmp.name, "out.csv")
        self.output_json = os_path.join(self.tmp.name, "out.json")
        self.manifest_path = os_path.join(self.tmp.name, "run.manifest.json")

    def _restore_cache_state(self) -> None:
        Ns_Cache.fpath_cname = self._original_fpath_cname
        Ns_Cache.info_changed = self._original_info_changed

    def _base_args(self, *extra: str) -> list[str]:
        return ["nsca", "lca", self.input_a, self.input_b, *extra]

    def test_lca_file_input_csv_and_json_manifest(self):
        for output_path, expected_format in ((self.output_csv, "csv"), (self.output_json, "json")):
            with self.subTest(output_format=expected_format):
                Ns_Cache.fpath_cname = {}
                _, success, err_msg = self.run_cli(
                    ["nsca", "lca", self.input_a, "-o", output_path, "--manifest", self.manifest_path]
                )
                self.assertTrue(success, msg=err_msg)

                manifest = self.load_manifest(self.manifest_path)
                self.assertEqual(manifest["analyzer"], "lca")
                self.assertEqual(manifest["version"], __version__)
                self.assertEqual(manifest["status"], "success")
                self.assertIsNone(manifest["error"])
                self.assertEqual(manifest["options"]["output_format"], expected_format)
                self.assertEqual(manifest["options"]["wordlist"], "bnc")
                self.assertEqual(manifest["options"]["tagset"], "ud")
                self.assertFalse(manifest["options"]["cache"])
                self.assertFalse(manifest["options"]["use_cache"])

                record = manifest["inputs"][0]
                self.assertEqual(list(record.keys()), FILE_RECORD_KEYS)
                self.assertEqual(record["path"], os_path.abspath(self.input_a))
                self.assertEqual(record["size"], os_path.getsize(self.input_a))
                self.assertEqual(record["sha256"], Ns_IO.sha256_file(self.input_a))
                self.assertIsNone(record["cache_path"])
                self.assertFalse(record["cache_hit"])

                self.assertEqual(manifest["output"]["sha256"], Ns_IO.sha256_file(output_path))

    def test_lca_text_input_keeps_only_identifier_and_hash(self):
        _, success, err_msg = self.run_cli(
            [
                "nsca",
                "lca",
                "--text",
                MARKER_TEXT,
                "-o",
                self.output_csv,
                "--manifest",
                self.manifest_path,
            ]
        )
        self.assertTrue(success, msg=err_msg)

        manifest = self.load_manifest(self.manifest_path)
        self.assertEqual(len(manifest["inputs"]), 1)
        text_record = manifest["inputs"][0]
        self.assertEqual(list(text_record.keys()), TEXT_RECORD_KEYS)
        self.assertEqual(text_record["identifier"], "cli_text")
        self.assertEqual(text_record["sha256"], Ns_IO.sha256_bytes(MARKER_TEXT.encode("utf-8")))
        with open(self.manifest_path, "rb") as f:
            self.assertNotIn(b"zzqmarkerzz", f.read())

    def test_lca_combine_subfiles_recorded(self):
        _, success, err_msg = self.run_cli(
            [
                "nsca",
                "lca",
                "-c",
                self.input_b,
                self.input_a,
                "-o",
                self.output_csv,
                "--manifest",
                self.manifest_path,
            ]
        )
        self.assertTrue(success, msg=err_msg)

        manifest = self.load_manifest(self.manifest_path)
        self.assertEqual(
            [record["path"] for record in manifest["inputs"]],
            sorted([os_path.abspath(self.input_a), os_path.abspath(self.input_b)]),
        )
        self.assertEqual(
            manifest["options"]["combine_files"],
            [[os_path.abspath(self.input_a), os_path.abspath(self.input_b)]],
        )

    def test_lca_cache_miss_records_cache_path_when_caching_enabled(self):
        _, success, err_msg = self.run_cli(
            self._base_args("--cache", "--use-cache", "-o", self.output_csv, "--manifest", self.manifest_path)
        )
        self.assertTrue(success, msg=err_msg)

        manifest = self.load_manifest(self.manifest_path)
        for record in manifest["inputs"]:
            self.assertFalse(record["cache_hit"])
            self.assertIsNotNone(record["cache_path"])
            self.assertTrue(record["cache_path"].endswith(Ns_Cache.CACHE_EXTENSION))

    def test_lca_cache_hit_is_recorded(self):
        input_abs = os_path.abspath(self.input_a)
        cache_path, is_available = Ns_Cache.get_cache_path(input_abs)
        self.assertFalse(is_available)
        with open(cache_path, "wb") as f:
            f.write(lzma.compress(b"fake serialized cache document"))
        future_mtime = os_path.getmtime(self.input_a) + 10
        os.utime(cache_path, (future_mtime, future_mtime))

        _, success, err_msg = self.run_cli(
            [
                "nsca",
                "lca",
                "--cache",
                "--use-cache",
                self.input_a,
                "-o",
                self.output_csv,
                "--manifest",
                self.manifest_path,
            ]
        )
        self.assertTrue(success, msg=err_msg)

        manifest = self.load_manifest(self.manifest_path)
        self.assertEqual(len(manifest["inputs"]), 1)
        record = manifest["inputs"][0]
        self.assertTrue(record["cache_hit"])
        self.assertEqual(record["cache_path"], os_path.abspath(cache_path))

    def test_lca_repeated_runs_are_identical_except_timestamps(self):
        argv = ["nsca", "lca", self.input_a, "-o", self.output_csv, "--manifest", self.manifest_path]
        _, success, first_err = self.run_cli(argv)
        self.assertTrue(success, msg=first_err)
        first = self.without_timestamps(self.load_manifest(self.manifest_path))

        _, success, second_err = self.run_cli(argv)
        self.assertTrue(success, msg=second_err)
        second = self.without_timestamps(self.load_manifest(self.manifest_path))

        self.assertEqual(first, second)

    def test_lca_failure_writes_failure_manifest(self):
        missing_output = os_path.join(self.tmp.name, "no_such_dir", "out.csv")
        _, success, err_msg = self.run_cli(
            self._base_args("-o", missing_output, "--manifest", self.manifest_path)
        )
        self.assertFalse(success)
        self.assertIn("FileNotFoundError", err_msg)

        manifest = self.load_manifest(self.manifest_path)
        self.assertEqual(manifest["status"], "failure")
        self.assertEqual(manifest["error"]["type"], "FileNotFoundError")
        self.assertIsNone(manifest["output"])
        self.assertFalse(os_path.exists(missing_output))


class TestManifestStdoutConflict(BaseTmpl, ManifestTestMixin):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manifest_path = os_path.join(self.tmp.name, "run.manifest.json")

    def test_stdout_manifest_conflict_rejected_for_both_analyzers(self):
        for subcommand in ("sca", "lca"):
            ui = Ns_Main_Cli()
            success, err_msg = ui.parse_args(
                [
                    "nsca",
                    subcommand,
                    "--text",
                    MARKER_TEXT,
                    "--stdout",
                    "--manifest",
                    self.manifest_path,
                ]
            )
            self.assertFalse(success)
            self.assertIsNotNone(err_msg)
            self.assertIn("manifest", err_msg.lower())
            self.assertIn("stdout", err_msg.lower())
            self.assertFalse(os_path.exists(self.manifest_path))


class TestManifestCommandLine(BaseTmpl, ManifestTestMixin):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = os.environ.copy()
        # SCA imports stanza before the --no-parse branch; provide a minimal
        # on-disk stub package for subprocesses where real stanza is absent.
        fake_deps = os_path.join(self.tmp.name, "fake_deps")
        os.makedirs(os_path.join(fake_deps, "stanza"))
        with open(os_path.join(fake_deps, "stanza", "__init__.py"), "w", encoding="utf-8") as stub_f:
            stub_f.write("class Document:\n    pass\n")
        self.env["PYTHONPATH"] = (
            str(SRC_DIR) + os.pathsep + fake_deps + os.pathsep + self.env.get("PYTHONPATH", "")
        )
        self.env["QT_QPA_PLATFORM"] = "offscreen"
        self.tree_path = self.write_file(self.tmp.name, "tree.txt", TREE_TEXT)
        self.text_path = self.write_file(self.tmp.name, "text.txt", MARKER_TEXT)

    def _run(self, cmd: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, capture_output=True, timeout=60, env=self.env)

    def test_subprocess_sca_manifest_is_written(self):
        output_path = os_path.join(self.tmp.name, "out.csv")
        manifest_path = os_path.join(self.tmp.name, "nested", "run.manifest.json")
        result = self._run(
            [
                sys.executable,
                "-m",
                "neosca",
                "sca",
                "--no-parse",
                self.tree_path,
                "-o",
                output_path,
                "--manifest",
                manifest_path,
            ]
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr.decode("utf-8"))
        self.assertTrue(os_path.isfile(output_path))
        manifest = self.load_manifest(manifest_path)
        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["output"]["sha256"], Ns_IO.sha256_file(output_path))
        leftovers = [name for name in os.listdir(os_path.dirname(manifest_path)) if name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_subprocess_stdout_manifest_conflict_exits_nonzero(self):
        manifest_path = os_path.join(self.tmp.name, "run.manifest.json")
        for subcommand, input_path in (("sca", self.tree_path), ("lca", self.text_path)):
            with self.subTest(subcommand=subcommand):
                extra = ["--no-parse"] if subcommand == "sca" else []
                result = self._run(
                    [
                        sys.executable,
                        "-m",
                        "neosca",
                        subcommand,
                        *extra,
                        input_path,
                        "--stdout",
                        "--manifest",
                        manifest_path,
                    ]
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("manifest", result.stderr.decode("utf-8").lower())
                self.assertFalse(os_path.exists(manifest_path))
