import argparse
import json
import logging
import os
import os.path as os_path
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from neosca.ns_about import __title__, __version__
from neosca.ns_consts import CACHE_DIR
from neosca.ns_io import Ns_Cache, Ns_IO
from neosca.ns_lca.ns_lca import Ns_LCA
from neosca.ns_print import color_print
from neosca.ns_sca.ns_sca import Ns_SCA
from neosca.ns_utils import Ns_Procedure_Result


class Ns_Main_Cli:
    def __init__(self) -> None:
        self.cwd = os.getcwd()
        self.args_parser: argparse.ArgumentParser = self.create_args_parser()
        self.options: argparse.Namespace = argparse.Namespace()

    def __add_log_levels(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--quiet",
            dest="is_quiet",
            action="store_true",
            default=False,
            help="disable all loggings",
        )
        parser.add_argument(
            "--verbose",
            dest="is_verbose",
            action="store_true",
            default=False,
            help="enable verbose loggings",
        )

    def create_args_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="nsca", formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument(
            "--version",
            action="store_true",
            default=False,
            help="show version and exit",
        )
        self.__add_log_levels(parser)
        subparsers: argparse._SubParsersAction = parser.add_subparsers(title="commands", dest="command")
        self.sca_parser = self.create_sca_parser(subparsers)
        self.lca_parser = self.create_lca_parser(subparsers)
        self.gui_parser = self.create_gui_parser(subparsers)
        self.cache_parser = self.create_cache_parser(subparsers)
        return parser

    def create_sca_parser(self, subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
        sca_parser = subparsers.add_parser("sca", help="syntactic complexity analyzer")

        sca_parser.add_argument(
            "--list",
            dest="list_fields",
            action="store_true",
            default=False,
            help="List built-in measures.",
        )
        sca_parser.add_argument(
            "--combine-files",
            "-c",
            metavar="<subfile>",
            dest="subfiles_list",
            action="append",
            default=None,
            nargs="+",
            help="Combine frequency output of multiple files.",
        )
        sca_parser.add_argument(
            "--text",
            "-t",
            metavar="<text>",
            default=None,
            help="Pass text through the command line.",
        )
        sca_parser.add_argument(
            "--ftype",
            dest="ifile_types",
            choices=Ns_IO.SUPPORTED_EXTENSIONS,
            default=Ns_IO.SUPPORTED_EXTENSIONS,
            nargs="+",
            help=(
                "Analyze files of the specified type(s). If not set, the program will process"
                " files of all supported types."
            ),
        )
        sca_parser.add_argument(
            "--output-file",
            "-o",
            metavar="<filename>",
            dest="ofile_freq",
            default=None,
            help='Specify an output file. The default is "result.csv".',
        )
        sca_parser.add_argument(
            "--output-format",
            dest="oformat_freq",
            choices=["csv", "json"],
            default="csv",
            help='Output format, the default is "csv".',
        )
        sca_parser.add_argument(
            "--stdout",
            dest="is_stdout",
            action="store_true",
            default=False,
            help="Write the frequency output to the stdout instead of saving it to a file.",
        )
        sca_parser.add_argument(
            "--select",
            metavar="<measure>",
            dest="selected_measures",
            default=None,
            nargs="+",
            help=(
                "Select only some of the measures to analyze. The builtin measures include"
                ' "W", "S", "VP", "C", "T", "DC", "CT", "CP", "CN", "MLS", "MLT", "MLC", "C/S",'
                ' "VP/T", "C/T", "DC/C", "DC/T", "T/S", "CT/T", "CP/T", "CP/C", "CN/T", and'
                ' "CN/C".'
            ),
        )
        sca_parser.add_argument(
            "--cache",
            dest="is_cache",
            action="store_true",
            default=False,
            help="Cache uncached files for faster future runs.",
        )
        sca_parser.add_argument(
            "--use-cache",
            dest="is_use_cache",
            action="store_true",
            default=False,
            help="Use cache if available.",
        )
        sca_parser.add_argument(
            "--save-matches",
            "-m",
            dest="is_save_matches",
            default=False,
            action="store_true",
            help="Save the matched subtrees.",
        )
        sca_parser.add_argument(
            "--no-parse",
            dest="is_skip_parsing",
            action="store_true",
            default=False,
            help=(
                "Assume input as parse trees. By default, the program expects"
                " raw text as input that will be parsed before querying. If you"
                " already have parsed input files, use this flag to indicate that"
                " the program should skip the parsing step and proceed directly"
                " to querying. When this flag is set, the --cache and --use-cache"
                " flags will be automatically set as False."
            ),
        )
        # parser_sca.add_argument(
        #     "--config",
        #     dest="config",
        #     default=None,
        #     help=(
        #         "Use custom json file where you can define your own syntactic structures to"
        #         " search or calculate."
        #     ),
        # )
        self.__add_log_levels(sca_parser)
        sca_parser.set_defaults(func=self.parse_sca_args, analyzer_class=Ns_SCA)
        return sca_parser

    def create_lca_parser(self, subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
        lca_parser: argparse.ArgumentParser = subparsers.add_parser("lca", help="lexical complexity analyzer")
        lca_parser.add_argument(
            "--list",
            dest="list_fields",
            action="store_true",
            default=False,
            help="List built-in measures.",
        )
        lca_parser.add_argument(
            "--combine-files",
            "-c",
            metavar="<subfile>",
            dest="subfiles_list",
            action="append",
            default=None,
            nargs="+",
            help="Combine frequency output of multiple files.",
        )
        lca_parser.add_argument(
            "--wordlist",
            dest="wordlist",
            choices=("bnc", "anc"),
            default="bnc",
            help=(
                "Choose BNC or ANC (American National Corpus) wordlist for lexical"
                ' sophistication analysis. The default is "bnc".'
            ),
        )
        lca_parser.add_argument(
            "--tagset",
            dest="tagset",
            choices=("ud", "ptb"),
            default="ud",
            help='Choose UD or PTB POS tagset for word classification. The default is "ud".',
        )
        lca_parser.add_argument(
            "--output-file",
            "-o",
            metavar="<filename>",
            dest="ofile_freq",
            default=None,
            help='Specify an output file. The default is "result.csv".',
        )
        lca_parser.add_argument(
            "--output-format",
            dest="oformat_freq",
            choices=["csv", "json"],
            default="csv",
            help='Output format, the default is "csv".',
        )
        lca_parser.add_argument(
            "--stdout",
            dest="is_stdout",
            action="store_true",
            default=False,
            help="Write the output to the stdout instead of saving it to a file.",
        )
        lca_parser.add_argument(
            "--text",
            "-t",
            metavar="<text>",
            default=None,
            help="Pass text through the command line.",
        )
        lca_parser.add_argument(
            "--cache",
            dest="is_cache",
            action="store_true",
            default=False,
            help="Cache uncached files for faster future runs.",
        )
        lca_parser.add_argument(
            "--use-cache",
            dest="is_use_cache",
            action="store_true",
            default=False,
            help="Use cache if available.",
        )
        lca_parser.add_argument(
            "--save-matches",
            "-m",
            dest="is_save_matches",
            default=False,
            action="store_true",
            help="Save the matched words.",
        )
        self.__add_log_levels(lca_parser)
        lca_parser.set_defaults(func=self.parse_lca_args, analyzer_class=Ns_LCA)
        return lca_parser

    def create_gui_parser(self, subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
        gui_parser = subparsers.add_parser("gui", help="start the program with GUI")
        self.__add_log_levels(gui_parser)
        gui_parser.set_defaults(is_gui=True)
        return gui_parser

    def create_cache_parser(self, subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
        cache_parser = subparsers.add_parser("cache", help="inspect and prune cached analysis files")
        self.__add_log_levels(cache_parser)
        cache_parser.set_defaults(is_cache_cmd=True)
        cache_subparsers: argparse._SubParsersAction = cache_parser.add_subparsers(
            title="cache commands", dest="cache_command"
        )

        list_parser = cache_subparsers.add_parser(
            "list", help="list cache entries with validity, outdated, missing and orphan status"
        )
        list_parser.add_argument(
            "--format",
            dest="oformat",
            choices=("text", "json"),
            default="text",
            help='Output format. The default is "text".',
        )
        list_parser.add_argument(
            "--output-file",
            "-o",
            metavar="<filename>",
            dest="ofile",
            default=None,
            help="Write the report to the given file instead of the stdout.",
        )
        self.__add_log_levels(list_parser)
        list_parser.set_defaults(is_cache_cmd=True, func=self.parse_cache_args)

        prune_parser = cache_subparsers.add_parser(
            "prune",
            help="prune missing, outdated or orphaned cache entries (dry run unless --apply is given)",
        )
        prune_parser.add_argument(
            "--status",
            dest="statuses",
            metavar="<status>",
            nargs="+",
            choices=Ns_Cache.PRUNABLE_STATUSES,
            default=None,
            help=(
                "Entry status(es) to prune. Choose any combination of"
                ' "missing", "outdated" and "orphaned". The default is all three.'
            ),
        )
        prune_parser.add_argument(
            "--apply",
            dest="is_apply",
            action="store_true",
            default=False,
            help=(
                "Actually delete the selected cache entries and update cache info."
                " Without this flag, only a dry-run preview is performed."
            ),
        )
        prune_parser.add_argument(
            "--format",
            dest="oformat",
            choices=("text", "json"),
            default="text",
            help='Output format. The default is "text".',
        )
        prune_parser.add_argument(
            "--output-file",
            "-o",
            metavar="<filename>",
            dest="ofile",
            default=None,
            help="Write the report to the given file instead of the stdout.",
        )
        self.__add_log_levels(prune_parser)
        prune_parser.set_defaults(is_cache_cmd=True, func=self.parse_cache_args)
        return cache_parser

    def parse_sca_args(self, options: argparse.Namespace) -> Ns_Procedure_Result:
        if options.is_skip_parsing:
            options.is_cache = False
            options.is_use_cache = False

        if options.text is not None:
            logging.debug(f"CLI text: {options.text}")

        if options.subfiles_list is None:
            self.verified_subfiles_list: list[list[str]] = []
        else:
            self.verified_subfiles_list = Ns_IO.get_verified_subfiles_list(options.subfiles_list)

        self.odir_matched = "neosca_sca_matches"
        if options.ofile_freq is not None:
            self.odir_matched = os_path.splitext(options.ofile_freq)[0] + "_matches"
            ofile_freq_ext = Ns_IO.suffix(options.ofile_freq, strip_dot=True)
            if ofile_freq_ext not in ("csv", "json"):
                return (
                    False,
                    f"The file extension {ofile_freq_ext} is not supported. Use one of the following:\n1. csv\n2. json",
                )
            if ofile_freq_ext != options.oformat_freq:
                logging.debug(
                    f"Conflict between ofile_freq ({options.ofile_freq}) and oformat_freq ({options.oformat_freq}), using {ofile_freq_ext}"
                )
                options.oformat_freq = ofile_freq_ext
        else:
            options.ofile_freq = f"neosca_sca_results.{options.oformat_freq}"

        if options.selected_measures is not None:
            # Drop duplicates while retain order. Starting from Python 3.7, the
            # built-in dictionary is guaranteed to maintain the insertion order
            options.selected_measures = list(dict.fromkeys(options.selected_measures))

        # user_config = options.config
        user_config = None
        if user_config is not None:
            if not os_path.isfile(user_config):
                return False, f"no such file as\n\n{user_config}"
            if not user_config.endswith(".json"):
                return False, f'"{user_config}" does not seem like a json file.'
            logging.debug(f"Using configuration file {user_config}")
        else:
            default_config_file = "nsca.json"
            if os_path.isfile(default_config_file):
                user_config = default_config_file
                logging.debug(f"Using configuration file {user_config}")
            else:
                logging.debug("No configuration file found")

        self.init_kwargs = {
            "ofile_freq": options.ofile_freq,
            "oformat_freq": options.oformat_freq,
            "odir_matched": self.odir_matched,
            "selected_measures": options.selected_measures,
            "is_cache": options.is_cache,
            "is_use_cache": options.is_use_cache,
            "is_save_matches": options.is_save_matches,
            "is_stdout": options.is_stdout,
            "is_skip_parsing": options.is_skip_parsing,
            "config": user_config,
        }
        return True, None

    def parse_lca_args(self, options: argparse.Namespace) -> Ns_Procedure_Result:
        self.odir_matched = "neosca_lca_matches"
        if options.ofile_freq is not None:
            self.odir_matched = os_path.splitext(options.ofile_freq)[0] + "_matches"
            ofile_freq_ext = Ns_IO.suffix(options.ofile_freq, strip_dot=True)
            if ofile_freq_ext not in ("csv", "json"):
                return (
                    False,
                    f"The file extension {ofile_freq_ext} is not supported. Use one of the following:\n1. csv\n2. json",
                )
            if ofile_freq_ext != options.oformat_freq:
                logging.debug(
                    f"Conflict between ofile_freq ({options.ofile_freq}) and oformat_freq ({options.oformat_freq}), using {ofile_freq_ext}"
                )
                options.oformat_freq = ofile_freq_ext
        else:
            options.ofile_freq = f"neosca_lca_results.{options.oformat_freq}"

        if options.text is not None:
            logging.debug(f"CLI text: {options.text}")

        if options.subfiles_list is None:
            self.verified_subfiles_list = []
        else:
            self.verified_subfiles_list = Ns_IO.get_verified_subfiles_list(options.subfiles_list)

        self.init_kwargs = {
            "wordlist": options.wordlist,
            "tagset": options.tagset,
            "ofile_freq": options.ofile_freq,
            "oformat_freq": options.oformat_freq,
            "odir_matched": self.odir_matched,
            "is_stdout": options.is_stdout,
            "is_cache": options.is_cache,
            "is_use_cache": options.is_use_cache,
            "is_save_matches": options.is_save_matches,
        }
        return True, None

    def parse_cache_args(self, options: argparse.Namespace) -> Ns_Procedure_Result:
        if getattr(options, "cache_command", None) == "prune":
            if options.statuses is None:
                options.statuses = list(Ns_Cache.PRUNABLE_STATUSES)
            else:
                # Drop duplicates while retaining order.
                options.statuses = list(dict.fromkeys(options.statuses))
        return True, None

    def parse_args(self, argv: list[str]) -> Ns_Procedure_Result:
        idx: int | None = None
        if "--" in argv[1:]:
            idx = argv.index("--")
        if idx is not None:
            options, ifile_list = self.args_parser.parse_args(argv[1:idx]), argv[idx + 1 :]
        else:
            options, ifile_list = self.args_parser.parse_known_args(argv[1:])

        assert not (options.is_quiet and options.is_verbose), (
            "logging cannot be quiet and verbose at the same time"
        )
        if options.is_quiet:
            logging.basicConfig(format="%(message)s", level=logging.CRITICAL)
        elif options.is_verbose:
            logging.basicConfig(format="%(message)s", level=logging.DEBUG)
        else:
            logging.basicConfig(format="%(message)s", level=logging.INFO)

        self.verified_ifiles = Ns_IO.get_verified_ifile_list(ifile_list)

        if (func := getattr(options, "func", None)) is not None:
            func(options)

        self.options = options
        return True, None

    def check_python(self) -> Ns_Procedure_Result:
        v_info = sys.version_info
        if v_info.minor >= 10 and v_info.major == 3:
            return True, None
        else:
            return (
                False,
                (
                    f"Error: Python {v_info.major}.{v_info.minor} is too old."
                    f" {__title__} only supports Python 3.10 or higher."
                ),
            )

    def exit_routine(self) -> None:
        Ns_Cache.save_cache_info()

        if self.options.is_quiet or self.options.is_stdout:
            return

        msg_num = 1
        color_print(
            "OKGREEN",
            f"{os_path.abspath(self.options.ofile_freq)}",
            prefix=f"{msg_num}. Frequency output was saved to ",
            postfix=".",
        )
        if self.options.is_cache and (self.verified_ifiles or self.verified_subfiles_list):
            msg_num += 1
            color_print("OKGREEN", str(CACHE_DIR), prefix=f"{msg_num}. Cache was saved to ", postfix=".")
        if self.options.is_save_matches:
            msg_num += 1
            color_print(
                "OKGREEN",
                f"{os_path.abspath(self.odir_matched)}",
                prefix=f"{msg_num}. Matches were saved to ",
                postfix=".",
            )
        if msg_num > 0:
            logging.info("Done.")

    def run_tmpl(func: Callable):  # type:ignore
        def wrapper(self, *args, **kwargs):
            sucess, err_msg = self.check_python()
            if not sucess:
                return sucess, err_msg
            if not self.options.is_stdout:
                sucess, err_msg = Ns_IO.is_writable(self.options.ofile_freq)
                if not sucess:
                    return sucess, err_msg
            func(self, *args, **kwargs)
            self.exit_routine()
            return True, None

        return wrapper

    @run_tmpl
    def run_on_input(self) -> Ns_Procedure_Result:
        analyzer = self.options.analyzer_class(**self.init_kwargs)

        if self.options.text is not None:
            analyzer.run_on_text(self.options.text)

        file_paths: list[str] = []
        for attr in ("verified_ifiles", "verified_subfiles_list"):
            if (paths := getattr(self, attr, None)) is not None:
                file_paths.extend(paths)

        if file_paths:
            analyzer.run_on_file_or_subfiles_list(file_paths)

        return True, None

    def run_gui(self) -> Ns_Procedure_Result:
        from neosca.ns_main_gui import main_gui

        main_gui()
        return True, None

    # ------------------------------------------------------------------
    # cache list / prune
    # ------------------------------------------------------------------
    def build_cache_list_payload(self) -> dict[str, Any]:
        entries = Ns_Cache.list_entries()
        return {
            "cache_dir": Ns_Cache.get_cache_dir(),
            "cache_info": Ns_Cache.get_cache_info_path(),
            "count": len(entries),
            "entries": entries,
        }

    def run_cache(self) -> Ns_Procedure_Result:
        options = self.options
        if getattr(options, "cache_command", None) is None:
            self.cache_parser.print_help()
            return True, None

        if options.cache_command == "list":
            payload = self.build_cache_list_payload()
            rendered = self.render_cache_payload(payload, options.oformat, is_prune=False)
        else:
            payload = Ns_Cache.prune_entries(options.statuses, apply=options.is_apply)
            rendered = self.render_cache_payload(payload, options.oformat, is_prune=True)
        self.write_cache_output(rendered, options.ofile)

        if options.cache_command == "prune" and payload["summary"]["failed"]:
            return (
                False,
                "Cache prune finished with errors. See the report for the failed entries.",
            )
        return True, None

    @staticmethod
    def _fit_column(text: str, width: int) -> str:
        if len(text) <= width:
            return text
        return "…" + text[-(width - 1) :]

    @staticmethod
    def _yes_or_no(value: bool) -> str:
        return "yes" if value else "no"

    def _render_cache_list_text(self, payload: dict[str, Any]) -> str:
        lines = [f"Cache root: {payload['cache_dir']}", f"Cache info: {payload['cache_info']}"]
        entries = payload["entries"]
        if not entries:
            lines.append("No cache entries found.")
            return "\n".join(lines)

        columns: list[tuple[str, Any, int]] = [
            ("INPUT PATH", lambda entry: entry["input_path"], 48),
            ("CACHE PATH", lambda entry: entry["cache_path"], 48),
            (
                "SIZE (BYTES)",
                lambda entry: "-" if entry["cache_size"] is None else str(entry["cache_size"]),
                14,
            ),
            (
                "MODIFIED (UTC)",
                lambda entry: entry["cache_mtime"] if entry["cache_mtime"] is not None else "-",
                27,
            ),
            ("CACHE EXISTS", lambda entry: self._yes_or_no(entry["cache_exists"]), 12),
            ("INPUT EXISTS", lambda entry: self._yes_or_no(entry["input_exists"]), 12),
            ("OUTDATED", lambda entry: self._yes_or_no(entry["is_outdated"]), 9),
            ("ORPHANED", lambda entry: self._yes_or_no(entry["is_orphaned"]), 9),
            ("STATUS", lambda entry: entry["status"], 10),
        ]
        fitted_rows = [
            [self._fit_column(str(selector(entry)), max_width) for _, selector, max_width in columns]
            for entry in entries
        ]
        widths = [
            max(len(header), *(len(row[col_idx]) for row in fitted_rows))
            for col_idx, (header, _, _) in enumerate(columns)
        ]
        lines.append("  ".join(header.ljust(widths[col_idx]) for col_idx, (header, _, _) in enumerate(columns)))
        for row in fitted_rows:
            lines.append("  ".join(cell.ljust(widths[col_idx]) for col_idx, cell in enumerate(row)))

        counts = {
            status: sum(entry["status"] == status for entry in entries) for status in Ns_Cache.ALL_STATUSES
        }
        lines.append(
            f"{len(entries)} entries: {counts[Ns_Cache.STATUS_VALID]} valid, "
            f"{counts[Ns_Cache.STATUS_MISSING]} missing, {counts[Ns_Cache.STATUS_OUTDATED]} outdated, "
            f"{counts[Ns_Cache.STATUS_ORPHANED]} orphaned."
        )
        return "\n".join(lines)

    def _render_cache_prune_text(self, payload: dict[str, Any]) -> str:
        lines = [
            f"Cache root: {payload['cache_dir']}",
            f"Cache info: {payload['cache_info']}",
            f"Selected statuses: {', '.join(payload['statuses'])}",
        ]
        if payload["apply"]:
            lines.append("Mode: apply (selected cache files are deleted)")
        else:
            lines.append("Mode: dry run (no changes; pass --apply to actually delete)")

        entries = payload["entries"]
        if not entries:
            lines.append("No cache entries matched the selected statuses.")
        for entry in entries:
            action = entry["action"]
            line = f"[{action:<12}] {str(entry['status']):<8} {entry['input_path']} -> {entry['cache_path']}"
            if entry.get("reason"):
                line += f"  ({entry['reason']})"
            lines.append(line)

        summary = payload["summary"]
        lines.append(
            f"Summary: {summary['selected']} selected, {summary['would_delete']} would delete, "
            f"{summary['deleted']} deleted, {summary['failed']} failed."
        )
        lines.append(f"Cache info updated: {'yes' if payload['metadata_updated'] else 'no'}")
        return "\n".join(lines)

    def render_cache_payload(self, payload: dict[str, Any], oformat: str, *, is_prune: bool) -> str:
        if oformat == "json":
            return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        if is_prune:
            return self._render_cache_prune_text(payload)
        return self._render_cache_list_text(payload)

    @staticmethod
    def write_cache_output(content: str, ofile: str | None) -> None:
        if ofile is None:
            sys.stdout.write(content + "\n")
            return
        out_path = Path(ofile)
        if out_path.parent != Path(""):
            out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content + "\n", encoding="utf-8")

    def run(self) -> Ns_Procedure_Result:
        if self.options.version:
            return self.show_version()
        elif getattr(self.options, "is_gui", False):
            return self.run_gui()
        elif getattr(self.options, "is_cache_cmd", False):
            return self.run_cache()
        elif getattr(self.options, "list_fields", False):
            return self.options.analyzer_class.list_fields()
        elif (
            getattr(self, "verified_ifiles", False)
            or getattr(self, "verified_subfiles_list", False)
            or getattr(self.options, "text", None) is not None
        ):
            return self.run_on_input()
        else:
            if (subparser := getattr(self, f"{self.options.command}_parser", None)) is not None:
                subparser.print_help()
            else:
                self.args_parser.print_help()
            return True, None

    def show_version(self) -> Ns_Procedure_Result:
        print(__version__)
        return True, None


def main_cli() -> None:
    ui = Ns_Main_Cli()
    success, err_msg = ui.parse_args(sys.argv)
    if not success:
        logging.critical(err_msg)
        sys.exit(1)
    success, err_msg = ui.run()
    if not success:
        logging.critical(err_msg)
        sys.exit(1)


if __name__ == "__main__":
    main_cli()
