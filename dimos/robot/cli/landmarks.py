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

"""CLI for managing landmark packs."""

from __future__ import annotations

from pathlib import Path

import typer

from dimos.landmark.landmark_pack import (
    _default_pack_dir,
    _landmark_summary,
    _load_landmarks_json,
    export_pack,
    import_pack,
    list_packs,
    load_manifest,
    status,
)

app = typer.Typer(help="Manage landmark packs", no_args_is_help=True)


@app.command("list")
def list_cmd() -> None:
    """List available landmark packs in fixtures/."""
    packs = list_packs()
    if not packs:
        typer.echo("No landmark packs found.")
        return
    for p in packs:
        typer.echo(p)


@app.command("show")
def show_cmd(
    pack_name: str = typer.Argument(..., help="Pack name to inspect"),
    pack_dir: Path = typer.Option(
        None, "--pack-dir", help="Custom pack directory (overrides default lookup)"
    ),
) -> None:
    """Print manifest and landmark summary for a pack."""
    pdir = pack_dir or _default_pack_dir(pack_name)
    if not pdir.is_dir():
        typer.echo(f"Pack not found: {pdir}", err=True)
        raise typer.Exit(1)

    try:
        manifest = load_manifest(pack_name, pack_dir=pdir)
    except (FileNotFoundError, ValueError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    typer.echo("─── Manifest ───")
    for k, v in manifest.items():
        if isinstance(v, dict):
            typer.echo(f"  {k}:")
            for sk, sv in v.items():
                typer.echo(f"    {sk}: {sv}")
        elif isinstance(v, list):
            typer.echo(f"  {k}:")
            for item in v:
                typer.echo(f"    - {item}")
        else:
            typer.echo(f"  {k}: {v}")

    try:
        records = _load_landmarks_json(pdir)
        typer.echo(f"\n─── Landmarks ({len(records)} total) ───")
        summary = _landmark_summary(records)
        for t, c in sorted(summary.items()):
            typer.echo(f"  {t}: {c}")
        for rec in records:
            name = rec.get("name", "(unnamed)")
            rtype = rec.get("record_type", "?")
            desc = rec.get("description", "")
            typer.echo(f"  [{rtype}] {name}" + (f" — {desc}" if desc else ""))
    except (FileNotFoundError, ValueError) as e:
        typer.echo(f"\n─── Landmarks ───\n  Error: {e}", err=True)
        raise typer.Exit(1)


@app.command("import")
def import_cmd(
    pack_name: str = typer.Argument(..., help="Pack name to import"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing landmark_memory/"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Only print what would be done"),
    no_backup: bool = typer.Option(False, "--no-backup", help="Skip backing up existing data"),
    clear_session_id: bool = typer.Option(
        True,
        "--clear-session-id/--keep-session-id",
        help="Clear session_id on import (default: clear)",
    ),
    pack_dir: Path = typer.Option(
        None, "--pack-dir", help="Custom pack directory (overrides default lookup)"
    ),
) -> None:
    """Import a landmark pack into the runtime directory."""
    try:
        result = import_pack(
            pack_name,
            force=force,
            dry_run=dry_run,
            clear_session_id=clear_session_id,
            no_backup=no_backup,
            pack_dir=pack_dir,
        )
    except RuntimeError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except FileExistsError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    if dry_run:
        typer.echo(f"[dry-run] {result.record_count} records would be imported")
        return

    typer.echo(f"Imported '{result.pack_name}': {result.record_count} records")
    if result.summary:
        for t, c in sorted(result.summary.items()):
            typer.echo(f"  {t}: {c}")
    if result.backup_dir:
        typer.echo(f"  Backup: {result.backup_dir}")
    if result.demo_queries:
        typer.echo("  Demo queries:")
        for q in result.demo_queries:
            typer.echo(f"    - {q}")


@app.command("export")
def export_cmd(
    pack_name: str = typer.Argument(..., help="Name for the exported pack"),
    output_dir: Path = typer.Option(
        None,
        "--output",
        "-o",
        help="Custom output directory (default: ~/.local/state/dimos/landmark_packs/<name>)",
    ),
) -> None:
    """Export current landmark_memory/ into a pack."""
    try:
        dest = export_pack(pack_name, output_dir=output_dir)
    except (FileNotFoundError, ValueError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Exported to {dest}")
    typer.echo("Edit manifest.yaml to fill in replay_db, demo_queries, etc.")


@app.command("status")
def status_cmd() -> None:
    """Show current landmark memory status."""
    s = status()
    if not s.exists:
        typer.echo("No landmark memory found.")
        typer.echo(f"  Expected at: {s.path}")
        return

    typer.echo(f"Path: {s.path}")
    typer.echo(f"Records: {s.record_count}")
    for t, c in sorted(s.summary.items()):
        typer.echo(f"  {t}: {c}")
