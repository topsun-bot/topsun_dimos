# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""``dimos nav`` offline diagnostics commands."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import typer

from dimos.core.run_registry import REGISTRY_DIR, RunEntry, is_pid_alive
from dimos.navigation.diagnostics.report import analyze_run

nav_app = typer.Typer(help="Offline navigation diagnostics", no_args_is_help=True)


@nav_app.command("analyze")
def analyze(
    run_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        resolve_path=True,
        help="Stopped DimOS run directory containing navigation/",
    ),
    session: str | None = typer.Option(
        None,
        "--session",
        help="Analyze only one navigation session ID",
    ),
    open_rerun: bool = typer.Option(
        False,
        "--open-rerun",
        help="Open the generated Rerun recording",
    ),
) -> None:
    """Generate JSON, Markdown, PNG, and Rerun reports after a run stops."""
    if _matching_live_run(run_dir):
        typer.echo(
            "Error: the run is still active; stop it before offline analysis.",
            err=True,
        )
        raise typer.Exit(2)
    try:
        reports = analyze_run(run_dir, session_id=session, create_rerun=True)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    for report in reports:
        typer.echo(str(report))
    if open_rerun:
        executable = shutil.which("rerun")
        if executable is None:
            typer.echo("Error: rerun executable not found.", err=True)
            raise typer.Exit(1)
        for report in reports:
            subprocess.Popen(
                [executable, str(report / "trace.rrd")],
                start_new_session=True,
            )


def _matching_live_run(run_dir: Path) -> bool:
    target = run_dir.expanduser().resolve()
    if not REGISTRY_DIR.is_dir():
        return False
    for path in REGISTRY_DIR.glob("*.json"):
        try:
            entry = RunEntry.load(path)
            if Path(entry.log_dir).expanduser().resolve() == target and is_pid_alive(entry.pid):
                return True
        except (OSError, TypeError, ValueError):
            continue
    return False
