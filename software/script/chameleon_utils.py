import argparse
import os.path
import subprocess
import sys
import tempfile
from collections.abc import Callable
from functools import wraps
from itertools import chain
from pathlib import Path
from typing import Any

import colorama
from prompt_toolkit.completion import Completer, NestedCompleter, WordCompleter
from prompt_toolkit.completion.base import Completion
from prompt_toolkit.document import Document

from chameleon_enum import Status

# Colorama shorthands
CR = colorama.Fore.RED
CG = colorama.Fore.GREEN
CB = colorama.Fore.BLUE
CC = colorama.Fore.CYAN
CY = colorama.Fore.YELLOW
CM = colorama.Fore.MAGENTA
C0 = colorama.Style.RESET_ALL


def get_resource_dir(relative_path: str) -> Path:
    """
    Get the resource directory of the program.
    Returns the temporary directory where files are extracted after being packaged with PyInstaller, or the directory where the script is located in the development environment.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base_dir = Path(sys._MEIPASS)
    else:
        base_dir = Path(__file__).parent
    return base_dir / relative_path


default_cwd = get_resource_dir("bin")


class ArgsParserError(Exception):
    pass


class ParserExitIntercept(Exception):
    pass


class UnexpectedResponseError(Exception):
    """
    Unexpected response exception
    """


class ArgumentParserNoExit(argparse.ArgumentParser):
    """
    If arg ArgumentParser parse error, we can't exit process,
    we must raise exception to stop parse
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_help = False
        self.description = "Please enter correct parameters"
        self.help_requested = False

    def exit(self, status: int = 0, message: str | None = None):
        if message:
            raise ParserExitIntercept(message)
            # status=0 means help was printed; raise to stop argparse continuing
            # to validate required args (which would cause a second print_help call)
        raise ParserExitIntercept("")

    def error(self, message: str):
        raise ArgsParserError(f"{self.prog}: error: {message}\n")


def print_help(self):
    """
    Colorize argparse help
    """
    print("-" * 80)
    print(color_string((CR, self.prog)))

    # Get the help text and split it, filtering out leading empty lines
    raw_lines = self.format_help().splitlines()
    lines = [line for line in raw_lines if line.strip() or line == ""]

    # Find the usage block safely
    usage_start = -1
    for i, line in enumerate(lines):
        if line.strip().startswith("usage:"):
            usage_start = i
            break

    if usage_start != -1:
        # We found a usage line, extract the block until the first empty line
        try:
            empty_after_usage = lines.index("", usage_start)
            usage = lines[usage_start:empty_after_usage]

            # Apply coloring to the usage string
            usage[0] = usage[0].replace("usage:", f"{color_string((CG, 'usage:'))}\n ")
            usage[0] = usage[0].replace(self.prog, color_string((CR, self.prog)))

            # Reformat indentation and print
            usage_to_print = [usage[0]] + [x[4:] for x in usage[1:]] + [""]
            print("\n".join(usage_to_print))

            # Advance lines pointer to after the usage block
            lines = lines[empty_after_usage + 1 :]
        except ValueError:
            # If no empty line found, just print what we have
            print("\n".join(lines[usage_start:]))
            lines = []

    # Print description if available
    if lines and lines[0].strip() != "":
        try:
            desc_end = lines.index("")
            desc = lines[:desc_end]
            print(color_string((CC, "\n".join(desc))))
            lines = lines[desc_end + 1 :]
        except ValueError:
            pass

    # Handle options and positional arguments without crashing on strict matches
    for line in lines:
        clean_line = line.strip().lower()
        if clean_line == "positional arguments:" or clean_line in [
            "options:",
            "optional arguments:",
        ]:
            print(color_string((CG, line)))
        else:
            print(line)

    print()
    self.help_requested = True


def print_mem_dump(bindata, blocksize):
    if blocksize <= 0:
        raise ValueError("blocksize must be greater than zero")

    hexadecimal_len = blocksize * 3 + 1
    ascii_len = blocksize + 1
    print(f"[=] ----+{hexadecimal_len * '-'}+{ascii_len * '-'}")
    print(f"[=] blk | data{(hexadecimal_len - 5) * ' '}| ascii")
    print(f"[=] ----+{hexadecimal_len * '-'}+{ascii_len * '-'}")

    for blk_index, offset in enumerate(range(0, len(bindata), blocksize), start=1):
        block = bindata[offset : offset + blocksize]
        hexstr = " ".join(f"{byte:02X}" for byte in block)
        asciistr = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in block)
        print(f"[=] {blk_index:3} | {hexstr} | {asciistr} ")


def print_key_table(key_map):
    key_a = key_map.get("A", {})
    key_b = key_map.get("B", {})
    sectors = sorted(set(key_a) | set(key_b))
    key_width = max(
        (len(str(key)) for key in chain(key_a.values(), key_b.values())),
        default=len("key A"),
    )
    header_line = f"[=] {'-' * 5}+{'-' * (key_width + 2)}+{'-' * (key_width + 2)}"
    print(header_line)
    print(f"[=]  sec | key A{' ' * (key_width - 5)} | key B{' ' * (key_width - 5)}")
    print(header_line)
    missing_key = "-" * key_width
    for sec in sectors:
        a = key_a.get(sec, missing_key)
        b = key_b.get(sec, missing_key)
        print(f"[=]  {sec:02d}  | {a:{key_width}} | {b:{key_width}}")
    print(header_line)


def _swap_endian(x):
    x = ((x >> 8) & 0x00FF00FF) | ((x & 0x00FF00FF) << 8)
    x = (x >> 16) | (x << 16)
    return x & 0xFFFFFFFF


def prng_successor(x, n):
    x = _swap_endian(x)

    for _ in range(n):
        x = (x >> 1) | ((((x >> 16) ^ (x >> 18) ^ (x >> 19) ^ (x >> 21)) & 0x1) << 31)
        x &= 0xFFFFFFFF

    return _swap_endian(x)


def reconstruct_full_nt(response_data, offset):
    nt = int.from_bytes(response_data[offset : offset + 2], byteorder="big")

    return (nt << 16) | prng_successor(nt, 16)


def parity_to_str(nt_par_err):
    return f"{nt_par_err & 0x0F:04b}"


def execute_tool(tool_name, args):
    if sys.platform == "win32":
        tool_executable = f"{tool_name}.exe"
    else:
        tool_executable = f"./{tool_name}"

    tool_path = os.path.join(default_cwd, tool_executable)
    cmd_recover_list = [tool_path]
    cmd_recover_list.extend(args)

    # print(f"Executing: {' '.join(cmd_recover_list)}")

    result = subprocess.run(
        cmd_recover_list,
        cwd=tempfile.gettempdir(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if result.returncode:
        raise RuntimeError("Failed to execute tool: " + result.stdout)

    return result.stdout


def tqdm_if_exists(iterator):
    try:
        import tqdm

        return tqdm.tqdm(iterator)
    except ImportError:
        return iterator


def expect_response(accepted_responses: int | list[int]) -> Callable[..., Any]:
    """
    Decorator for wrapping a Chameleon CMD function to check its response
    for expected return codes and throwing an exception otherwise
    """
    accepted = frozenset(
        [accepted_responses]
        if isinstance(accepted_responses, int)
        else accepted_responses
    )

    def decorator(func):
        @wraps(func)
        def error_throwing_func(*args, **kwargs):
            ret = func(*args, **kwargs)
            if ret.status not in accepted:
                try:
                    status_string = str(Status(ret.status))
                except ValueError:
                    status_string = (
                        f"Unexpected response and unknown status {ret.status}"
                    )
                raise UnexpectedResponseError(status_string)

            return ret.parsed

        return error_throwing_func

    return decorator


def color_string(*args):
    return "".join(f"{color}{text}" for color, text in args) + C0


class CLITree:
    """
    Class holding a

    :param name: Name of the command (e.g. "set")
    :param help_text: Hint displayed for the command
    :param fullname: Full name of the command that includes previous commands (e.g. "hw settings animation")
    :param cls: A BaseCLIUnit instance handling the command
    """

    def __init__(
        self,
        name: str = "",
        help_text: str | None = None,
        fullname: str | None = None,
        children: list["CLITree"] | None = None,
        cls=None,
        root=False,
    ) -> None:
        self.name = name
        self.help_text = help_text
        self.fullname = fullname if fullname else name
        self.children = [] if children is None else children
        self.cls = cls
        self.root = root
        if self.help_text is None and not root:
            assert self.cls is not None
            parser = self.cls().args_parser()
            assert parser is not None
            self.help_text = parser.description

    def subgroup(self, name, help_text=None):
        """
        Create a child command group

        :param name: Name of the command group
        :param help_text: Hint displayed for the group
        """
        child = CLITree(
            name=name,
            fullname=f"{self.fullname} {name}" if not self.root else f"{name}",
            help_text=help_text,
        )
        self.children.append(child)
        return child

    def command(self, name):
        """
        Create a child command

        :param name: Name of the command
        """

        def decorator(cls):
            self.children.append(
                CLITree(
                    name=name,
                    fullname=f"{self.fullname} {name}" if not self.root else f"{name}",
                    cls=cls,
                )
            )
            return cls

        return decorator


class CustomNestedCompleter(NestedCompleter):
    """
    Copy of the NestedCompleter class that accepts a CLITree object and
    supports meta_dict for descriptions
    """

    def __init__(
        self, options, ignore_case: bool = True, meta_dict: dict | None = None
    ) -> None:
        self.options = options
        self.ignore_case = ignore_case
        self.meta_dict = {} if meta_dict is None else meta_dict
        self._word_completer = WordCompleter(
            tuple(self.options),
            ignore_case=self.ignore_case,
            meta_dict=self.meta_dict,
        )

    def __repr__(self) -> str:
        return (
            f"CustomNestedCompleter({self.options!r}, ignore_case={self.ignore_case!r})"
        )

    @classmethod
    def from_clitree(cls, node):
        options = {}
        meta_dict = {}

        for child_node in node.children:
            if child_node.cls:
                # CLITree is a standalone command with arguments
                options[child_node.name] = ArgparseCompleter(
                    child_node.cls().args_parser()
                )
            else:
                # CLITree is a command group
                options[child_node.name] = cls.from_clitree(child_node)
                meta_dict[child_node.name] = child_node.help_text

        return cls(options, meta_dict=meta_dict)

    def get_completions(self, document, complete_event):
        # Split document.
        text = document.text_before_cursor.lstrip()
        stripped_len = len(document.text_before_cursor) - len(text)

        # If there is a space, check for the first term, and use a sub_completer.
        if " " in text:
            first_term = text.split()[0]
            completer = self.options.get(first_term)

            # If we have a sub completer, use this for the completions.
            if completer is not None:
                remaining_text = text[len(first_term) :].lstrip()
                move_cursor = len(text) - len(remaining_text) + stripped_len

                new_document = Document(
                    remaining_text,
                    cursor_position=document.cursor_position - move_cursor,
                )

                yield from completer.get_completions(new_document, complete_event)

        # No space in the input: behave exactly like `WordCompleter`.
        else:
            yield from self._word_completer.get_completions(document, complete_event)


class ArgparseCompleter(Completer):
    """
    Completer instance for autocompletion of ArgumentParser arguments

    :param parser: ArgumentParser instance
    """

    def __init__(self, parser) -> None:
        self.parser: ArgumentParserNoExit = parser

    def check_tokens(self, parsed, unparsed):
        suggestions = {}

        def check_arg(tokens):
            return tokens and tokens[0].startswith("-")

        if not parsed and not unparsed:
            # No tokens detected, just show all flags
            for action in self.parser._actions:
                for opt in action.option_strings:
                    suggestions[opt] = action.help
            return [], [], suggestions

        token = unparsed.pop(0)

        for action in self.parser._actions:
            if any(opt == token for opt in action.option_strings):
                # Argument fully matches the token
                parsed.append(token)

                if action.choices:
                    # Autocomplete with choices
                    if unparsed:
                        # Autocomplete values
                        value = unparsed.pop(0)
                        for choice in action.choices:
                            if str(choice).startswith(value):
                                suggestions[str(choice)] = None

                        parsed.append(value)

                        if check_arg(unparsed):
                            parsed, unparsed, suggestions = self.check_tokens(
                                parsed, unparsed
                            )

                    else:
                        # Show all possible values
                        for choice in action.choices:
                            suggestions[str(choice)] = None

                    break
                else:
                    # No choices, process further arguments
                    if check_arg(unparsed):
                        parsed, unparsed, suggestions = self.check_tokens(
                            parsed, unparsed
                        )
                    break
            elif any(opt.startswith(token) for opt in action.option_strings):
                for opt in action.option_strings:
                    if opt.startswith(token):
                        suggestions[opt] = action.help

        if suggestions:
            unparsed.insert(0, token)

        return parsed, unparsed, suggestions

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        word_before_cursor = document.text_before_cursor.split(" ")[-1]

        _, _, suggestions = self.check_tokens([], text.split())

        for key, suggestion in suggestions.items():
            yield Completion(
                key, -len(word_before_cursor), display=key, display_meta=suggestion
            )
