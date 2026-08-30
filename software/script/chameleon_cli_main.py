#!/usr/bin/env python3
import argparse
import io
import pathlib
import shlex
import sys
import traceback
from collections import deque

import colorama
import prompt_toolkit
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import FileHistory

import chameleon_cli_unit
import chameleon_com
import chameleon_utils
from chameleon_utils import CG, CR, CY, color_string

ULTRA = r"""
                                                                ╦ ╦╦ ╔╦╗╦═╗╔═╗
                                                   ███████      ║ ║║  ║ ╠╦╝╠═╣
                                                                ╚═╝╩═╝╩ ╩╚═╩ ╩
"""

LITE = r"""
                                                                ╦  ╦╔╦╗╔═╗
                                                   ███████      ║  ║ ║ ║╣
                                                                ╩═╝╩ ╩ ╚═╝
"""

# create by http://patorjk.com/software/taag/#p=display&f=ANSI%20Shadow&t=Chameleon%20Ultra
BANNER = """
 ██████╗██╗  ██╗ █████╗ ██╗   ██╗███████╗██╗     ███████╗ █████╗ ██╗  ██╗
██╔════╝██║  ██║██╔══██╗███╗ ███║██╔════╝██║     ██╔════╝██╔══██╗███╗ ██║
██║     ███████║███████║████████║█████╗  ██║     █████╗  ██║  ██║████╗██║
██║     ██╔══██║██╔══██║██╔██╔██║██╔══╝  ██║     ██╔══╝  ██║  ██║██╔████║
╚██████╗██║  ██║██║  ██║██║╚═╝██║███████╗███████╗███████╗╚█████╔╝██║╚███║
 ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝   ╚═╝╚══════╝╚══════╝╚══════╝ ╚════╝ ╚═╝ ╚══╝
"""


class ChameleonCLI:
    """
    CLI for chameleon
    """

    def __init__(self):
        # new a device communication instance(only communication)
        self.device_com = chameleon_com.ChameleonCom()

    def get_cmd_node(
        self, node: chameleon_utils.CLITree, cmdline: list[str]
    ) -> tuple[chameleon_utils.CLITree, list[str]]:
        """
        Recursively traverse the command line tree to get to the matching node

        :return: last matching CLITree node, remaining tokens
        """
        # No more subcommands to parse, return node
        if not cmdline:
            return node, []

        for child in node.children:
            if cmdline[0] == child.name:
                return self.get_cmd_node(child, cmdline[1:])

        # No matching child node
        return node, cmdline

    def get_prompt(self):
        """
        Retrieve the cli prompt

        :return: current cmd prompt
        """
        if self.device_com.isOpen():
            status = color_string((CG, "USB"))
        else:
            status = color_string((CR, "Offline"))

        return ANSI(f"[{status}] chameleon --> ")

    @staticmethod
    def print_banner():
        """
            print chameleon ascii banner.

        :return:
        """
        print(color_string((CY, BANNER)))

    @staticmethod
    def _close_argument_files(args: argparse.Namespace) -> None:
        standard_streams = {
            stream
            for stream in (
                sys.stdin,
                sys.stdout,
                sys.stderr,
                getattr(sys.stdin, "buffer", None),
                getattr(sys.stdout, "buffer", None),
                getattr(sys.stderr, "buffer", None),
            )
            if stream is not None
        }
        closed_ids = set()
        for value in vars(args).values():
            if (
                isinstance(value, io.IOBase)
                and value not in standard_streams
                and id(value) not in closed_ids
            ):
                value.close()
                closed_ids.add(id(value))

    def exec_cmd(self, cmd_str):
        cmd_str = cmd_str.strip()
        if not cmd_str:
            return

        # look for alternate exit
        if cmd_str in {"quit", "q", "e"}:
            cmd_str = "exit"

        # look for alternate comments
        if cmd_str[0] in ";#%":
            cmd_str = "rem " + cmd_str[1:].lstrip()

        # parse cmd
        try:
            argv = shlex.split(cmd_str)
        except ValueError as exc:
            print(color_string((CR, f"Invalid command line: {exc}")))
            return

        tree_node, arg_list = self.get_cmd_node(chameleon_cli_unit.root, argv)
        if not tree_node.cls:
            # Found tree node is a group without an implementation, print children
            print("".ljust(18, "-") + "".ljust(10) + "".ljust(30, "-"))
            for child in tree_node.children:
                cmd_title = color_string((CG, child.name))
                if not child.cls:
                    help_line = (
                        f" - {cmd_title}".ljust(37)
                    ) + f"{{ {child.help_text}... }}"
                else:
                    help_line = (f" - {cmd_title}".ljust(37)) + f"{child.help_text}"
                print(help_line)
            return

        unit: chameleon_cli_unit.BaseCLIUnit = tree_node.cls()
        unit.device_com = self.device_com
        args_parse_result = unit.args_parser()

        assert args_parse_result is not None
        args: argparse.ArgumentParser = args_parse_result
        args.prog = tree_node.fullname
        try:
            args_parse_result = args.parse_args(arg_list)
            if args.help_requested:
                return
        except chameleon_utils.ArgsParserError as e:
            args.print_help()
            print(color_string((CY, str(e).strip())))
            return
        except chameleon_utils.ParserExitIntercept:
            # don't exit process.
            return
        try:
            # before process cmd, we need to do something...
            if not unit.before_exec(args_parse_result):
                return

            try:
                unit.on_exec(args_parse_result)
            finally:
                unit.after_exec(args_parse_result)

        except (
            chameleon_utils.UnexpectedResponseError,
            chameleon_utils.ArgsParserError,
        ) as e:
            print(color_string((CR, str(e))))
        except Exception:
            print(f"CLI exception: {color_string((CR, traceback.format_exc()))}")
        finally:
            self._close_argument_files(args_parse_result)

    def startCLI(self):
        """
            start listen input.

        :return:
        """
        self.completer = chameleon_utils.CustomNestedCompleter.from_clitree(
            chameleon_cli_unit.root
        )
        self.session = prompt_toolkit.PromptSession(
            completer=self.completer,
            history=FileHistory(str(pathlib.Path.home() / ".chameleon_history")),
        )

        self.print_banner()
        queued_commands = deque()
        while True:
            if queued_commands:
                cmd_str = queued_commands.popleft()
            else:
                # wait user input
                try:
                    cmd_str = self.session.prompt(self.get_prompt()).strip()
                    queued_commands.extend(
                        cmd_str.replace("\r\n", "\n").replace("\r", "\n").split("\n")
                    )
                    cmd_str = queued_commands.popleft()
                except (EOFError, KeyboardInterrupt):
                    cmd_str = "exit"
            self.exec_cmd(cmd_str)


if __name__ == "__main__":
    colorama.init(autoreset=True)
    chameleon_cli_unit.check_tools()
    ChameleonCLI().startCLI()
