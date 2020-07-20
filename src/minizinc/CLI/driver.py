#  This Source Code Form is subject to the terms of the Mozilla Public
#  License, v. 2.0. If a copy of the MPL was not distributed with this
#  file, You can obtain one at http://mozilla.org/MPL/2.0/.

import asyncio
import re
import subprocess
import warnings
from datetime import timedelta
from pathlib import Path
from typing import Any, List, Optional, Set, Type, Union

import minizinc

from ..driver import Driver
from ..error import ConfigurationError, parse_error
from ..solver import Solver


def to_python_type(mzn_type: dict) -> Type:
    """Converts MiniZinc JSON type to Type

    Converts a MiniZinc JSON type definition generated by the MiniZinc CLI to a
    Python Type object. This can be used on types that result from calling
    ``minizinc --model-interface-only``.

    Args:
        mzn_type (dict): MiniZinc type definition as resulting from JSON

    Returns:
        Type: Type definition in Python

    """
    basetype = mzn_type["type"]
    pytype: Type
    # TODO: MiniZinc does not report enumerated types correctly
    if basetype == "bool":
        pytype = bool
    elif basetype == "float":
        pytype = float
    elif basetype == "int":
        pytype = int
    else:
        warnings.warn(
            f"Unable to determine minizinc type `{basetype}` assuming integer type",
            FutureWarning,
        )
        pytype = int

    if mzn_type.get("set", False):
        if pytype is int:
            pytype = Union[Set[int], range]  # type: ignore
        else:
            pytype = Set[pytype]  # type: ignore

    dim = mzn_type.get("dim", 0)
    while dim >= 1:
        # No typing support for n-dimensional typing
        pytype = List[pytype]  # type: ignore
        dim -= 1
    return pytype


class CLIDriver(Driver):
    """Driver that interfaces with MiniZinc through the command line interface.

    The command line driver will interact with MiniZinc and its solvers through
    the use of a ``minizinc`` executable. Driving MiniZinc using its executable
    is non-incremental and can often trigger full recompilation and might
    restart the solver from the beginning when changes are made to the instance.

    Attributes:
        executable (Path): The path to the executable used to access the MiniZinc Driver

    """

    _executable: Path

    def __init__(self, executable: Path):
        self._executable = executable
        assert self._executable.exists()

        super(CLIDriver, self).__init__()

    def make_default(self) -> None:
        from . import CLIInstance

        minizinc.default_driver = self
        minizinc.Instance = CLIInstance

    def run(
        self,
        args: List[Any],
        solver: Optional[Solver] = None,
        timeout: Optional[timedelta] = None,
    ):
        # TODO: Add documentation
        timeout_flt = None
        if timeout is not None:
            timeout_flt = timeout.total_seconds()
        if solver is None:
            cmd = [str(self._executable), "--allow-multiple-assignments"] + [
                str(arg) for arg in args
            ]
            minizinc.logger.debug(
                f"CLIDriver:run -> command: \"{' '.join(cmd)}\", timeout "
                f"{timeout_flt}"
            )
            output = subprocess.run(
                cmd,
                stdin=None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_flt,
            )
        else:
            with solver.configuration() as conf:
                cmd = [
                    str(self._executable),
                    "--solver",
                    conf,
                    "--allow-multiple-assignments",
                ] + [str(arg) for arg in args]
                minizinc.logger.debug(
                    f"CLIDriver:run -> command: \"{' '.join(cmd)}\", timeout "
                    f"{timeout_flt}"
                )
                output = subprocess.run(
                    cmd,
                    stdin=None,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=timeout_flt,
                )
        if output.returncode != 0:
            raise parse_error(output.stderr)
        return output

    async def create_process(self, args: List[str], solver: Optional[Solver] = None):
        # TODO: Add documentation
        if solver is None:
            minizinc.logger.debug(
                f"CLIDriver:create_process -> program: {str(self._executable)} "
                f'args: "--allow-multiple-assignments '
                f"{' '.join(str(arg) for arg in args)}\""
            )
            proc = await asyncio.create_subprocess_exec(
                str(self._executable),
                "--allow-multiple-assignments",
                *[str(arg) for arg in args],
                stdin=None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            with solver.configuration() as conf:
                minizinc.logger.debug(
                    f"CLIDriver:create_process -> program: {str(self._executable)} "
                    f'args: "--solver {conf} --allow-multiple-assignments '
                    f"{' '.join(str(arg) for arg in args)}\""
                )
                proc = await asyncio.create_subprocess_exec(
                    str(self._executable),
                    "--solver",
                    conf,
                    "--allow-multiple-assignments",
                    *[str(arg) for arg in args],
                    stdin=None,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
        return proc

    @property
    def minizinc_version(self) -> str:
        return self.run(["--version"]).stdout.decode()

    def check_version(self):
        output = self.run(["--version"])
        match = re.search(rb"version (\d+)\.(\d+)\.(\d+)", output.stdout)
        found = tuple([int(i) for i in match.groups()])
        if found < minizinc.driver.required_version:
            raise ConfigurationError(
                f"The MiniZinc driver found at '{self._executable}' has "
                f"version {found}. The minimal required version is "
                f"{minizinc.driver.required_version}."
            )
