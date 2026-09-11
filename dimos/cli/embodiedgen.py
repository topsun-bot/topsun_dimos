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

"""CLI for the optional EmbodiedGen asset bridge."""

from __future__ import annotations

from pathlib import Path

import typer

from dimos.simulation.embodiedgen.compose import (
    compose_standalone_scene,
    genesis_entities,
)
from dimos.simulation.embodiedgen.discovery import (
    AssetPlacement,
    discover_assets,
    parse_placements,
    register_asset,
)
from dimos.simulation.embodiedgen.paths import catalog_dir, embodiedgen_export_dir, embodiedgen_root
from dimos.simulation.embodiedgen.runner import cli_status, run_cli

app = typer.Typer(
    help="Register EmbodiedGen exports and compose DimOS sim scenes", no_args_is_help=True
)


@app.command("status")
def status_cmd() -> None:
    """Show EmbodiedGen path config and whether an optional CLI is installed."""
    status = cli_status()
    typer.echo(f"embodiedgen_root: {embodiedgen_root() or '(unset)'}")
    typer.echo(f"embodiedgen_export_dir: {embodiedgen_export_dir() or '(unset)'}")
    typer.echo(f"catalog_dir: {catalog_dir()}")
    typer.echo(f"cli: {status.detail}")


@app.command("list")
def list_cmd(
    export_dir: Path | None = typer.Option(
        None, "--export-dir", help="Override EMBODIEDGEN_EXPORT_DIR for this listing"
    ),
) -> None:
    """List discovered EmbodiedGen assets in the export dir and catalog."""
    assets = discover_assets(export_dir=export_dir)
    if not assets:
        typer.echo("No EmbodiedGen assets found.")
        raise typer.Exit(0)
    for asset in assets:
        kinds = []
        if asset.mjcf is not None:
            kinds.append(f"mjcf={asset.mjcf}")
        if asset.urdf is not None:
            kinds.append(f"urdf={asset.urdf}")
        if asset.usd is not None:
            kinds.append(f"usd={asset.usd}")
        typer.echo(f"{asset.name}\t{asset.source}\t{asset.root}\t{'; '.join(kinds)}")


@app.command("register")
def register_cmd(
    path: Path = typer.Argument(..., help="EmbodiedGen export folder or URDF/MJCF/USD file"),
    name: str | None = typer.Option(None, "--name", help="Catalog name (default: inferred)"),
) -> None:
    """Copy an EmbodiedGen export into the DimOS catalog (~/.local/share/dimos/embodiedgen)."""
    try:
        asset = register_asset(path, name=name)
    except (FileNotFoundError, ValueError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Registered {asset.name} -> {asset.root}")
    typer.echo(f"Load: dimos --simulation --mujoco-embodiedgen-assets={asset.name} run unitree-go2")


@app.command("scene")
def scene_cmd(
    spec: str = typer.Argument(..., help="Asset placement, e.g. eg_box or eg_box@1,0,0.2"),
    engine: str = typer.Option("mujoco", "--engine", help="mujoco or genesis"),
    export_dir: Path | None = typer.Option(None, "--export-dir"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write MuJoCo XML here"),
) -> None:
    """Compose a MuJoCo scene XML or print Genesis entity JSON for a registered asset."""
    placements = parse_placements(spec)
    if not placements:
        placements = [AssetPlacement(name=spec)]
    try:
        if engine == "genesis":
            entities = genesis_entities(placements, export_dir=export_dir)
            typer.echo(repr(entities))
            return
        if engine != "mujoco":
            typer.echo(f"Error: unsupported engine {engine!r} (use mujoco or genesis)", err=True)
            raise typer.Exit(1)
        composed = compose_standalone_scene(placements, export_dir=export_dir)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    if output is not None:
        output.write_text(composed.xml)
        typer.echo(f"Wrote {output}")
        return
    typer.echo(composed.xml)


@app.command("run-cli")
def run_cli_cmd(
    cli_name: str = typer.Option("img3d-cli", "--cli", help="EmbodiedGen console script name"),
    args: list[str] = typer.Argument(None, help="Arguments forwarded to the EmbodiedGen CLI"),
) -> None:
    """Run an EmbodiedGen CLI if installed. Fails clearly when it is not."""
    try:
        result = run_cli(list(args or []), name=cli_name)
    except FileNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2)
    if result.stdout:
        typer.echo(result.stdout, nl=False)
    if result.stderr:
        typer.echo(result.stderr, err=True, nl=False)
    raise typer.Exit(result.returncode)
