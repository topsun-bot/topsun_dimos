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

"""Small macOS-friendly GUI for capturing Go2 truth trajectory DBs."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import shlex
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Any

from dimos.robot.unitree.go2.cli.truth_capture import (
    _terminate_process,
    build_capture_command,
    build_capture_manifest,
    default_db_path,
    inspect_truth_db,
)


class TruthCaptureGui:
    """Tkinter wrapper around the Go2 truth DB capture command."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Go2-W Truth Trajectory Capture")
        self.root.geometry("900x740")
        self.root.minsize(800, 660)

        self._events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._process: subprocess.Popen[str] | None = None
        self._db_path: Path | None = None
        self._manifest_path: Path | None = None
        self._stop_requested = False
        self._check_thresholds: tuple[float, int, int, int] | None = None

        self.robot_ip = tk.StringVar(value="192.168.123.161")
        self.route = tk.StringVar(value="office_loop")
        self.out_dir = tk.StringVar(value=str(Path("data/truth/go2").resolve()))
        self.viewer = tk.StringVar(value="rerun")
        self.scene = tk.StringVar(value="")
        self.task_text = tk.StringVar(value="")
        self.teleop_user = tk.StringVar(value="")
        self.map_ref = tk.StringVar(value="")
        self.min_duration = tk.StringVar(value="180")
        self.min_odom = tk.StringVar(value="1000")
        self.min_lidar = tk.StringVar(value="300")
        self.min_images = tk.StringVar(value="300")
        self.save_rerun = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Ready. Fill Go2-W IP, then click Start Recording.")

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._poll_events)

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        header = ttk.Frame(self.root, padding=(16, 14, 16, 6))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        title = ttk.Label(header, text="Go2-W 真值轨迹采集", font=("Helvetica", 22, "bold"))
        title.grid(row=0, column=0, sticky="w")
        subtitle = ttk.Label(
            header,
            text="上位机启动录制, 人工遥控绕楼一圈, 停止后生成 benchmark 真值 DB。",
        )
        subtitle.grid(row=1, column=0, sticky="w", pady=(4, 0))

        form = ttk.LabelFrame(self.root, text="Capture Settings", padding=12)
        form.grid(row=1, column=0, sticky="ew", padx=16, pady=8)
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)

        ttk.Label(form, text="Go2-W IP").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.robot_ip).grid(row=0, column=1, sticky="ew", padx=(8, 16))
        ttk.Label(form, text="Route").grid(row=0, column=2, sticky="w")
        ttk.Entry(form, textvariable=self.route).grid(row=0, column=3, sticky="ew", padx=(8, 0))

        ttk.Label(form, text="Output Dir").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(form, textvariable=self.out_dir).grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=(8, 8), pady=(8, 0)
        )
        ttk.Button(form, text="Browse", command=self._browse_out_dir).grid(
            row=1, column=3, sticky="e", pady=(8, 0)
        )

        ttk.Label(form, text="Viewer").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(
            form,
            textvariable=self.viewer,
            values=("rerun", "none", "foxglove"),
            state="readonly",
            width=12,
        ).grid(row=2, column=1, sticky="w", padx=(8, 16), pady=(8, 0))
        ttk.Checkbutton(form, text="Save Rerun .rrd evidence", variable=self.save_rerun).grid(
            row=2, column=2, columnspan=2, sticky="w", pady=(8, 0)
        )

        ttk.Label(form, text="Scene").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(form, textvariable=self.scene).grid(
            row=3, column=1, sticky="ew", padx=(8, 16), pady=(8, 0)
        )
        ttk.Label(form, text="Operator").grid(row=3, column=2, sticky="w", pady=(8, 0))
        ttk.Entry(form, textvariable=self.teleop_user).grid(
            row=3, column=3, sticky="ew", padx=(8, 0), pady=(8, 0)
        )

        ttk.Label(form, text="Task").grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(form, textvariable=self.task_text).grid(
            row=4, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=(8, 0)
        )

        ttk.Label(form, text="Map Ref").grid(row=5, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(form, textvariable=self.map_ref).grid(
            row=5, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=(8, 0)
        )

        thresholds = ttk.Frame(form)
        thresholds.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        for index in range(8):
            thresholds.columnconfigure(index, weight=1)

        ttk.Label(thresholds, text="Min seconds").grid(row=0, column=0, sticky="w")
        ttk.Entry(thresholds, textvariable=self.min_duration, width=8).grid(
            row=0, column=1, sticky="w"
        )
        ttk.Label(thresholds, text="Min odom").grid(row=0, column=2, sticky="w")
        ttk.Entry(thresholds, textvariable=self.min_odom, width=8).grid(row=0, column=3, sticky="w")
        ttk.Label(thresholds, text="Min lidar").grid(row=0, column=4, sticky="w")
        ttk.Entry(thresholds, textvariable=self.min_lidar, width=8).grid(
            row=0, column=5, sticky="w"
        )
        ttk.Label(thresholds, text="Min images").grid(row=0, column=6, sticky="w")
        ttk.Entry(thresholds, textvariable=self.min_images, width=8).grid(
            row=0, column=7, sticky="w"
        )

        body = ttk.Frame(self.root, padding=(16, 0, 16, 8))
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        self.log = scrolledtext.ScrolledText(body, height=16, wrap="word", state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew")

        controls = ttk.Frame(self.root, padding=(16, 0, 16, 16))
        controls.grid(row=3, column=0, sticky="ew")
        controls.columnconfigure(2, weight=1)

        self.start_button = ttk.Button(
            controls,
            text="Start Recording",
            command=self._start_capture,
        )
        self.start_button.grid(row=0, column=0, padx=(0, 8))

        self.stop_button = ttk.Button(
            controls,
            text="Stop and Check",
            command=self._stop_capture,
            state="disabled",
        )
        self.stop_button.grid(row=0, column=1, padx=(0, 8))

        ttk.Label(controls, textvariable=self.status).grid(row=0, column=2, sticky="w")

    def _browse_out_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.out_dir.get() or ".")
        if selected:
            self.out_dir.set(selected)

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _validate_inputs(self) -> tuple[Path, float, int, int, int] | None:
        robot_ip = self.robot_ip.get().strip()
        route = self.route.get().strip()
        if not robot_ip:
            messagebox.showerror("Missing Go2-W IP", "Please enter the Go2-W IP address.")
            return None
        if not route:
            messagebox.showerror("Missing route", "Please enter a route label.")
            return None

        try:
            min_duration = float(self.min_duration.get())
            min_odom = int(self.min_odom.get())
            min_lidar = int(self.min_lidar.get())
            min_images = int(self.min_images.get())
        except ValueError:
            messagebox.showerror(
                "Invalid thresholds",
                "Min seconds, odom, lidar, and images must be numeric.",
            )
            return None
        if min_duration < 0 or min_odom < 0 or min_lidar < 0 or min_images < 0:
            messagebox.showerror(
                "Invalid thresholds",
                "Minimum duration and stream counts cannot be negative.",
            )
            return None

        out_dir = Path(self.out_dir.get()).expanduser().resolve()
        return (out_dir, min_duration, min_odom, min_lidar, min_images)

    def _start_capture(self) -> None:
        validated = self._validate_inputs()
        if validated is None:
            return

        out_dir, min_duration, min_odom, min_lidar, min_images = validated
        out_dir.mkdir(parents=True, exist_ok=True)
        self._check_thresholds = (min_duration, min_odom, min_lidar, min_images)

        self._db_path = default_db_path(self.route.get().strip(), out_dir).resolve()
        self._manifest_path = self._db_path.with_suffix(".manifest.json")
        rrd_dir = out_dir / "rrd" if self.save_rerun.get() else None
        if rrd_dir is not None:
            rrd_dir.mkdir(parents=True, exist_ok=True)

        cmd = build_capture_command(
            robot_ip=self.robot_ip.get().strip(),
            db_path=self._db_path,
            viewer=self.viewer.get(),  # type: ignore[arg-type]
            overwrite=False,
            save_rerun=self.save_rerun.get(),
            rrd_dir=rrd_dir,
        )

        manifest = build_capture_manifest(
            route=self.route.get().strip(),
            robot_ip=self.robot_ip.get().strip(),
            db_path=self._db_path,
            manifest_path=self._manifest_path,
            viewer=self.viewer.get(),  # type: ignore[arg-type]
            save_rerun=self.save_rerun.get(),
            rrd_dir=rrd_dir,
            scene=self.scene.get().strip(),
            task_text=self.task_text.get().strip(),
            teleop_user=self.teleop_user.get().strip(),
            map_ref=self.map_ref.get().strip(),
            command=cmd,
            robot_variant="go2-w",
        )
        self._write_json(self._manifest_path, manifest)

        self._stop_requested = False
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="disabled")
        self.status.set("Starting DimOS capture...")
        self._append_log("\n=== Starting Go2-W truth capture ===\n")
        self._append_log("DB: " + str(self._db_path) + "\n")
        self._append_log("Command: " + shlex.join(cmd) + "\n\n")

        start_error = self._launch_capture_process(cmd)
        if start_error is not None:
            manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
            manifest["return_code"] = None
            manifest["start_error"] = start_error
            manifest["check_ok"] = False
            manifest["failed_checks"] = [f"Capture process failed to start: {start_error}"]
            self._write_json(self._manifest_path, manifest)
            self._append_log(f"Failed to start capture: {start_error}\n")
            self.status.set("Capture failed to start. Check the error above.")
            self._reset_buttons()
            messagebox.showerror("Capture error", f"Failed to start capture: {start_error}")
            return

        assert self._process is not None
        process = self._process
        self.stop_button.configure(state="normal")
        self.status.set("Recording. Use the remote controller to drive one full loop.")
        worker = threading.Thread(
            target=self._read_capture_process,
            args=(process,),
            daemon=True,
        )
        worker.start()
        messagebox.showinfo(
            "Recording started",
            "Recording has started. Drive Go2-W around the office loop, then click Stop and Check.",
        )

    def _launch_capture_process(self, cmd: list[str]) -> str | None:
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            self._process = None
            return str(exc)
        return None

    def _read_capture_process(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            self._events.put(("log", line))

        return_code = process.wait()
        self._events.put(("process_exit", return_code))

    def _stop_capture(self) -> None:
        if self._process is None or self._process.poll() is not None:
            return

        self._stop_requested = True
        self.status.set("Stopping DimOS capture and checking DB...")
        self.stop_button.configure(state="disabled")
        self._append_log("\n=== Stop requested ===\n")

        def terminate() -> None:
            assert self._process is not None
            _terminate_process(self._process)

        threading.Thread(target=terminate, daemon=True).start()

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self._events.get_nowait()
                self._handle_event(kind, payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _handle_event(self, kind: str, payload: Any) -> None:
        if kind == "log":
            self._append_log(str(payload))
        elif kind == "error":
            self._append_log(str(payload) + "\n")
            messagebox.showerror("Capture error", str(payload))
        elif kind == "process_exit":
            self._on_process_exit(int(payload))

    def _on_process_exit(self, return_code: int, *, notify: bool = True) -> None:
        if self._db_path is None:
            self._reset_buttons()
            return

        self._append_log(f"\n=== Capture process exited: {return_code} ===\n")
        if self._manifest_path is not None and self._manifest_path.exists():
            manifest = json.loads(self._manifest_path.read_text(encoding="utf-8"))
            manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
            manifest["return_code"] = return_code
            manifest["stop_requested"] = self._stop_requested
            self._write_json(self._manifest_path, manifest)

        summary = self._check_db()
        if self._manifest_path is not None and self._manifest_path.exists():
            manifest = json.loads(self._manifest_path.read_text(encoding="utf-8"))
            manifest["check_path"] = str(self._db_path.with_suffix(".check.json"))
            manifest["check_ok"] = bool(summary.get("ok"))
            manifest["duration_s"] = float(summary.get("duration_s", 0.0))
            manifest["start_pose"] = summary.get("start_pose")
            manifest["failed_checks"] = summary.get("failed_checks", [])
            self._write_json(self._manifest_path, manifest)
        self._reset_buttons()

        if summary.get("ok"):
            self.status.set("Truth DB captured and checked successfully.")
            if notify:
                messagebox.showinfo(
                    "Truth DB ready",
                    "Truth DB captured and passed checks:\n\n" + str(self._db_path),
                )
        else:
            self.status.set("Capture finished, but DB check failed. See log.")
            if notify:
                messagebox.showwarning(
                    "DB check failed",
                    "Capture finished, but DB check failed. See the log and .check.json.",
                )

    def _check_db(self) -> dict[str, Any]:
        assert self._db_path is not None
        if self._check_thresholds is None:
            summary: dict[str, Any] = {
                "db_path": str(self._db_path),
                "ok": False,
                "failed_checks": ["Capture thresholds are unavailable"],
            }
        else:
            min_duration, min_odom, min_lidar, min_images = self._check_thresholds
            summary = inspect_truth_db(
                self._db_path,
                min_duration_s=min_duration,
                min_counts={
                    "odom": min_odom,
                    "lidar": min_lidar,
                    "color_image": min_images,
                },
            )
        self._write_json(self._db_path.with_suffix(".check.json"), summary)
        self._append_log("\n=== Truth DB check ===\n")
        self._append_log(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
        return summary

    def _reset_buttons(self) -> None:
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self._process = None
        self._stop_requested = False
        self._check_thresholds = None

    def _on_close(self) -> None:
        process = self._process
        if process is not None:
            if process.poll() is None:
                if not messagebox.askyesno(
                    "Stop capture?",
                    "A capture is still running. Stop it, validate its DB, and close the app?",
                ):
                    return
                self._stop_requested = True
                self.status.set("Stopping capture before close...")
                _terminate_process(process)
            return_code = process.returncode if process.returncode is not None else -1
            self._on_process_exit(return_code, notify=False)
        self.root.destroy()

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def main() -> None:
    root = tk.Tk()
    TruthCaptureGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
