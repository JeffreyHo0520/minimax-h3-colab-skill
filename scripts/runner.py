#!/usr/bin/env python3
"""Run MiniMax H3 reference-to-video jobs on one reusable Colab session."""

from __future__ import annotations

import argparse
import json
import math
import os
import queue
import random
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable


SKILL_DIR = Path(__file__).resolve().parents[1]
NOTEBOOK = SKILL_DIR / "assets" / "MiniMax_H3_Turbo_Colab.ipynb"
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PICTURE_RE = re.compile(r"<Picture\s+(\d+)>")
NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")
AUTH = os.environ.get("COLAB_AUTH", "oauth2")


class ColabCommandError(RuntimeError):
    def __init__(self, label: str, returncode: int, output: list[str]):
        self.label = label
        self.returncode = returncode
        self.output = output
        tail = "\n".join(line for line in output[-12:] if line)
        super().__init__(f"{label} failed (exit {returncode})" + (f":\n{tail}" if tail else ""))


class ColabTimeoutError(TimeoutError):
    pass


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def clean_output(value: str) -> str:
    return ANSI_RE.sub("", value).replace("\r", "").strip()


def write_progress(path: Path | None, state: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def parse_usage(output: str) -> dict[str, Any]:
    """Parse labelled `colab usage` output; v0.7.4 has no JSON option."""
    normalized = clean_output(output)

    def number(pattern: str, label: str) -> float:
        match = re.search(pattern, normalized, re.IGNORECASE | re.MULTILINE)
        if not match:
            raise ValueError(f"colab usage output did not contain {label!r}.")
        return float(match.group(1).replace(",", ""))

    balance = number(r"^Current balance:\s*([\d,]+(?:\.\d+)?)\s+compute units\s*$", "Current balance")
    rate = number(r"^Usage rate:\s*([\d,]+(?:\.\d+)?)\s*/\s*hr\s*$", "Usage rate")
    assignments = number(r"^Active assignments:\s*(\d+)\s*$", "Active assignments")
    return {
        "balance": balance,
        "rate_per_hour": rate,
        "active_assignments": int(assignments),
        "checked_at": now_iso(),
    }


def _colab_path() -> str:
    path = shutil.which("colab")
    if not path:
        raise FileNotFoundError("Colab CLI is not installed or is not on PATH. Install google-colab-cli and sign in with OAuth2 first.")
    return path


def call_colab(
    arguments: list[str],
    *,
    label: str,
    timeout: float,
    on_line: Callable[[str], None] | None = None,
) -> str:
    command = [_colab_path(), f"--auth={AUTH}", *arguments]
    child = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        start_new_session=True,
    )
    lines: queue.Queue[str | None] = queue.Queue()

    def collect() -> None:
        assert child.stdout is not None
        for line in child.stdout:
            lines.put(line.rstrip("\n"))
        lines.put(None)

    reader = threading.Thread(target=collect, daemon=True)
    reader.start()
    output: list[str] = []
    eof = False
    started = time.monotonic()
    while not eof or child.poll() is None:
        if time.monotonic() - started > timeout and child.poll() is None:
            if sys.platform == "win32":
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
            else:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                    child.wait(timeout=5)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            reader.join(timeout=2)
            if child.stdout is not None:
                child.stdout.close()
            raise ColabTimeoutError(f"{label} exceeded {timeout:g} seconds. The remote kernel may still be busy; the batch will stop its Colab session instead of retrying and duplicating compute.")
        try:
            item = lines.get(timeout=0.25)
        except queue.Empty:
            continue
        if item is None:
            eof = True
            continue
        line = clean_output(item)
        output.append(line)
        if on_line:
            on_line(line)
    returncode = child.wait()
    reader.join(timeout=2)
    if child.stdout is not None:
        child.stdout.close()
    if returncode != 0:
        raise ColabCommandError(label, returncode, output)
    return "\n".join(line for line in output if line)


def check_cli() -> dict[str, str]:
    version = call_colab(["version"], label="colab version", timeout=20)
    return {"version": version, "auth": AUTH}


def get_usage() -> dict[str, Any]:
    output = call_colab(["usage"], label="colab usage", timeout=30)
    return parse_usage(output)


def start_session(
    session: str,
    gpu: str,
    high_mem: bool,
    progress_path: Path | None = None,
) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session):
        raise ValueError("Session name may contain only letters, numbers, underscores, and hyphens (up to 64 characters).")
    state: dict[str, Any] = {"task": "start", "status": "starting", "session": session, "gpu": gpu, "log_tail": [], "updated_at": now_iso()}
    write_progress(progress_path, state)

    def on_line(line: str) -> None:
        state["log_tail"] = (state["log_tail"] + ([line] if line else []))[-30:]
        state["updated_at"] = now_iso()
        write_progress(progress_path, state)

    args = ["new", "--session", session, "--gpu", gpu]
    if high_mem:
        args.append("--high-mem")
    try:
        output = call_colab(args, label="create Colab session", timeout=900, on_line=on_line)
        state.update({"status": "active", "session": session, "output": output, "updated_at": now_iso()})
        write_progress(progress_path, state)
    except Exception as exc:
        state.update({"status": "failed", "error": str(exc), "updated_at": now_iso()})
        write_progress(progress_path, state)
        raise


def stop_session(session: str, progress_path: Path | None = None) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session):
        raise ValueError("Invalid Colab session name.")
    state: dict[str, Any] = {"task": "stop", "status": "stopping", "session": session, "log_tail": [], "updated_at": now_iso()}
    write_progress(progress_path, state)

    def on_line(line: str) -> None:
        state["log_tail"] = (state["log_tail"] + ([line] if line else []))[-30:]
        state["updated_at"] = now_iso()
        write_progress(progress_path, state)

    try:
        output = call_colab(["stop", "--session", session], label="stop Colab session", timeout=300, on_line=on_line)
        state.update({"status": "stopped", "output": output, "updated_at": now_iso()})
        write_progress(progress_path, state)
    except Exception as exc:
        state.update({"status": "failed", "error": str(exc), "updated_at": now_iso()})
        write_progress(progress_path, state)
        raise


def safe_job_id(value: Any) -> str:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,48}", value):
        return value
    return uuid.uuid4().hex[:16]


def safe_name(value: Any, fallback: str) -> str:
    candidate = NAME_RE.sub("_", str(value or "")).strip("_-")[:64]
    return candidate or fallback


def shot_timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    minutes, remainder = divmod(milliseconds, 60_000)
    whole_seconds, millis = divmod(remainder, 1_000)
    return f"{minutes:02d}:{whole_seconds:02d}.{millis:03d}"


def compose_ref2va_prompt(
    *,
    subject_definitions: str,
    summary: str,
    retention_analysis: str,
    shots: list[dict[str, Any]],
    overall_soundscape: str,
    non_diegetic_music: str,
    image_count: int,
    duration_seconds: float,
    detailed_description: str = "",
) -> str:
    """Build the complete Ref2VA prompt used by guided web-UI jobs."""
    if not 1 <= image_count <= 9:
        raise ValueError("Ref2VA prompts require 1–9 reference images.")
    if not math.isfinite(duration_seconds) or not 4 <= duration_seconds <= 15:
        raise ValueError("Video duration must be between 4 and 15 seconds.")
    if not isinstance(shots, list) or not shots:
        raise ValueError("Add at least one shot.")

    sections = {
        "subject_definitions": subject_definitions,
        "summary": summary,
        "retention_analysis": retention_analysis,
        "overall_soundscape": overall_soundscape,
        "non_diegetic_music": non_diegetic_music,
    }
    for label, value in sections.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label.replace('_', ' ')} cannot be empty.")

    details: list[str] = []
    previous_start = -1.0
    for number, shot in enumerate(shots, start=1):
        if not isinstance(shot, dict):
            raise ValueError(f"Shot {number} must be an object.")
        try:
            start = float(shot.get("start_seconds"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Shot {number} needs a numeric start time.") from exc
        description = shot.get("description")
        if not math.isfinite(start) or not 0 <= start < duration_seconds:
            raise ValueError(f"Shot {number} must start between 0 and the video duration.")
        if number == 1 and start != 0:
            raise ValueError("Shot 1 must start at 0 seconds.")
        if start <= previous_start:
            raise ValueError("Shot start times must be strictly increasing.")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"Shot {number} needs a description.")
        if number > 1 and round(start * 1000) <= round(previous_start * 1000):
            raise ValueError("Shot start times must differ by at least 0.001 seconds.")
        rendered = description.strip()
        dialogue = str(shot.get("dialogue") or "").strip()
        if dialogue:
            # Dialogue is intentionally a single Chinese line so it can safely
            # be represented by the model's spoken-audio marker.
            dialogue = " ".join(dialogue.split())
            if "</d>" in dialogue:
                raise ValueError(f"Shot {number} dialogue contains a reserved closing marker.")
            speaker = str(shot.get("speaker") or "The subject (S1)").strip()
            rendered += f" {speaker} says, <d>[Chinese] {dialogue}</d>"
        if number == 1:
            details.append(f"[Shot 1] {rendered}")
        else:
            transition_starters = (
                "the camera cuts to", "the shot cuts to", "the shot transitions to",
                "the shot changes to", "the shot switches to",
            )
            transition = rendered if rendered.lower().startswith(transition_starters) else "the camera cuts to " + rendered
            details.append(f"[Shot {number}] At {shot_timestamp(start)}, {transition}")
        previous_start = start

    detail_parts = ([detailed_description.strip()] if detailed_description.strip() else []) + details
    prompt = (
        "subject_definitions:\n" + subject_definitions.strip() + "\n\n"
        "summary:\n" + summary.strip() + "\n\n"
        "retention_analysis:\n" + retention_analysis.strip() + "\n\n"
        "detailed_description:\n" + "\n\n".join(detail_parts) + "\n\n"
        "overall_soundscape:\n" + overall_soundscape.strip() + "\n\n"
        "non_diegetic_music:\n" + non_diegetic_music.strip()
    )
    invalid = sorted({int(number) for number in PICTURE_RE.findall(prompt) if int(number) < 1 or int(number) > image_count})
    if invalid:
        raise ValueError(f"Prompt references Picture {invalid}; this job has {image_count} images.")
    return prompt


def resolve_jobs(manifest: dict[str, Any], output_dir: Path) -> list[dict[str, Any]]:
    jobs = manifest.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ValueError("Manifest must contain a non-empty jobs list.")
    if len(jobs) > 20:
        raise ValueError("A single batch may contain at most 20 videos.")
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_outputs: set[Path] = set()
    for index, raw in enumerate(jobs, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"Job {index} must be an object.")
        refs = raw.get("reference_images", raw.get("images"))
        if not isinstance(refs, list) or not 1 <= len(refs) <= 9:
            raise ValueError(f"Job {index} must have 1–9 reference images.")
        image_paths: list[Path] = []
        for image in refs:
            path = Path(str(image)).expanduser().resolve()
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError(f"Reference image is missing or empty: {path}")
            image_paths.append(path)
        prompt_file = raw.get("prompt_file")
        if prompt_file:
            prompt_path = Path(str(prompt_file)).expanduser().resolve()
            if not prompt_path.is_file() or prompt_path.stat().st_size == 0:
                raise ValueError(f"Prompt file is missing or empty: {prompt_path}")
            prompt = prompt_path.read_text(encoding="utf-8")
        else:
            prompt = str(raw.get("prompt", ""))
        if not prompt.strip():
            raise ValueError(f"Job {index} needs a non-empty UTF-8 prompt.")
        image_count = len(image_paths)
        invalid = sorted({int(n) for n in PICTURE_RE.findall(prompt) if int(n) < 1 or int(n) > image_count})
        if invalid:
            raise ValueError(f"Job {index} prompt references Picture {invalid}; it has {image_count} images.")
        duration = float(raw.get("duration_seconds", raw.get("duration", 12)))
        if not math.isfinite(duration) or not 4 <= duration <= 15:
            raise ValueError(f"Job {index} duration must be between 4 and 15 seconds.")
        seed = raw.get("seed")
        seed = random.SystemRandom().randrange(0, 2**64) if seed in (None, "") else int(seed)
        if not 0 <= seed < 2**64:
            raise ValueError(f"Job {index} seed must be between 0 and 2^64-1.")
        job_id = safe_job_id(raw.get("id"))
        if job_id in seen_ids:
            raise ValueError(f"Job {index} duplicates job id {job_id!r}.")
        seen_ids.add(job_id)
        stem = safe_name(raw.get("output_name") or raw.get("title"), f"h3_{index:02d}")
        output = Path(str(raw.get("output_path") or output_dir / f"{stem}_{job_id}.mp4")).expanduser().resolve()
        if output in seen_outputs:
            raise ValueError(f"Multiple jobs target the same output path: {output}")
        seen_outputs.add(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        resolved.append({
            "id": job_id,
            "title": str(raw.get("title") or f"Video {index}")[:120],
            "prompt": prompt,
            "reference_images": image_paths,
            "duration_seconds": duration,
            "seed": seed,
            "output_path": output,
        })
    return resolved


def verify_mp4(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise FileNotFoundError(f"Downloaded MP4 is missing or empty: {path}")
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return
    probe = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if probe.returncode != 0:
        raise ValueError(f"Downloaded output is not a readable MP4: {clean_output(probe.stderr)}")
    streams = {item.get("codec_type") for item in json.loads(probe.stdout).get("streams", [])}
    if not {"video", "audio"} <= streams:
        raise ValueError(f"Downloaded MP4 must contain video and audio streams; found {sorted(streams)}.")


def run_batch(
    manifest_path: Path,
    *,
    session: str | None,
    gpu: str,
    high_mem: bool,
    stop_on_complete: bool,
    progress_path: Path | None,
    output_dir: Path,
    exec_timeout: float,
    create_session_if_named: bool = False,
) -> dict[str, Any]:
    if not NOTEBOOK.is_file():
        raise FileNotFoundError(f"Bundled inference notebook not found: {NOTEBOOK}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    jobs = resolve_jobs(manifest, output_dir)
    owns_session = session is None or create_session_if_named
    if session is None:
        session = f"h3-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{uuid.uuid4().hex[:6]}"
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session):
        raise ValueError("Invalid Colab session name.")

    progress: dict[str, Any] = {
        "task": "batch",
        "status": "starting",
        "session": session,
        "gpu": gpu,
        "jobs": [{"id": job["id"], "title": job["title"], "status": "queued", "output": str(job["output_path"])} for job in jobs],
        "log_tail": [],
        "updated_at": now_iso(),
    }
    write_progress(progress_path, progress)

    def log_line(line: str) -> None:
        # Avoid copying full prompt text into logs/state files.
        if not line or len(line) > 320 or "<Picture" in line or "<Subject" in line or "[Chinese]" in line:
            return
        progress["log_tail"] = (progress["log_tail"] + [line])[-30:]
        progress["updated_at"] = now_iso()
        write_progress(progress_path, progress)

    results: list[dict[str, Any]] = []
    batch_error: str | None = None
    work_root = manifest_path.parent / f"work_{uuid.uuid4().hex[:8]}"
    work_root.mkdir(parents=True, exist_ok=True)
    try:
        if owns_session:
            start_session(session, gpu, high_mem)
        progress["status"] = "running"
        progress["updated_at"] = now_iso()
        write_progress(progress_path, progress)

        for index, job in enumerate(jobs):
            current = progress["jobs"][index]
            current.update({"status": "uploading", "started_at": now_iso()})
            progress["current_job"] = job["id"]
            progress["updated_at"] = now_iso()
            write_progress(progress_path, progress)
            remote_refs: list[str] = []
            remote_prefix = f"/content/h3_{job['id']}"
            for image_index, image_path in enumerate(job["reference_images"], start=1):
                remote = f"{remote_prefix}_reference_{image_index}.img"
                call_colab(
                    ["upload", "--session", session, str(image_path), remote],
                    label=f"upload reference {image_index} for {job['title']}",
                    timeout=600,
                    on_line=log_line,
                )
                remote_refs.append(remote)

            prompt_path = work_root / f"{job['id']}.txt"
            prompt_path.write_text(job["prompt"], encoding="utf-8")
            remote_prompt = f"{remote_prefix}_prompt.txt"
            remote_output = f"{remote_prefix}_output.mp4"
            call_colab(
                ["upload", "--session", session, str(prompt_path), remote_prompt],
                label=f"upload prompt for {job['title']}",
                timeout=120,
                on_line=log_line,
            )
            job_notebook_dir = work_root / job["id"]
            job_notebook_dir.mkdir(parents=True, exist_ok=True)
            notebook_copy = job_notebook_dir / "MiniMax_H3_Turbo_Colab.ipynb"
            shutil.copy2(NOTEBOOK, notebook_copy)
            env_values = [
                "H3_INFERENCE_MODE=reference",
                "H3_REFERENCE_IMAGES=" + json.dumps(remote_refs, separators=(",", ":")),
                "H3_PROMPT_FILE=" + remote_prompt,
                "H3_DURATION_SECONDS=" + str(job["duration_seconds"]),
                "H3_SEED=" + str(job["seed"]),
                "H3_OUTPUT_PREFIX=MiniMax_H3_" + job["id"][:20],
                "H3_OUTPUT_PATH=" + remote_output,
                "H3_JOB_TIMEOUT_SECONDS=" + str(min(exec_timeout, 7200)),
            ]
            exec_args = ["exec", "--session", session, "--timeout", str(exec_timeout)]
            for value in env_values:
                exec_args.extend(["--env", value])
            exec_args.extend(["--file", str(notebook_copy)])
            current.update({"status": "generating", "seed": job["seed"], "duration_seconds": job["duration_seconds"]})
            progress["updated_at"] = now_iso()
            write_progress(progress_path, progress)
            call_colab(exec_args, label=f"generate {job['title']}", timeout=exec_timeout + 60, on_line=log_line)

            current["status"] = "downloading"
            progress["updated_at"] = now_iso()
            write_progress(progress_path, progress)
            call_colab(
                ["download", "--session", session, remote_output, str(job["output_path"])],
                label=f"download {job['title']}",
                timeout=900,
                on_line=log_line,
            )
            verify_mp4(job["output_path"])
            current.update({"status": "completed", "finished_at": now_iso(), "bytes": job["output_path"].stat().st_size})
            results.append({"id": job["id"], "output": str(job["output_path"]), "status": "completed"})
            progress["updated_at"] = now_iso()
            write_progress(progress_path, progress)
    except ColabTimeoutError as exc:
        batch_error = str(exc)
        if "current_job" in progress:
            current = next((item for item in progress["jobs"] if item["id"] == progress["current_job"]), None)
            if current and current["status"] not in {"completed", "failed"}:
                current.update({"status": "failed", "error": batch_error, "finished_at": now_iso()})
        for item in progress["jobs"]:
            if item["status"] == "queued":
                item["status"] = "cancelled"
    except Exception as exc:
        batch_error = str(exc)
        if "current_job" in progress:
            current = next((item for item in progress["jobs"] if item["id"] == progress["current_job"]), None)
            if current and current["status"] not in {"completed", "failed"}:
                current.update({"status": "failed", "error": batch_error, "finished_at": now_iso()})
        for item in progress["jobs"]:
            if item["status"] == "queued":
                item["status"] = "cancelled"
    finally:
        if (owns_session or stop_on_complete) and session:
            progress["status"] = "stopping_session"
            progress["updated_at"] = now_iso()
            write_progress(progress_path, progress)
            try:
                stop_session(session)
                progress["session_status"] = "stopped"
            except Exception as exc:
                progress["session_status"] = "stop_failed"
                progress["cleanup_error"] = str(exc)
                batch_error = batch_error or f"Batch ended, but Colab session {session} could not be stopped: {exc}"
        elif not owns_session:
            progress["session_status"] = "active"
        shutil.rmtree(work_root, ignore_errors=True)

    completed = sum(item["status"] == "completed" for item in progress["jobs"])
    failed = sum(item["status"] == "failed" for item in progress["jobs"])
    cancelled = sum(item["status"] == "cancelled" for item in progress["jobs"])
    progress.update({
        "status": "completed" if not batch_error and completed == len(jobs) else "partial" if completed else "failed",
        "completed_count": completed,
        "failed_count": failed,
        "cancelled_count": cancelled,
        "error": batch_error,
        "updated_at": now_iso(),
        "results": results,
    })
    write_progress(progress_path, progress)
    return progress


def main() -> int:
    parser = argparse.ArgumentParser(description="MiniMax H3 Colab session and batch runner")
    sub = parser.add_subparsers(dest="command", required=True)

    usage_parser = sub.add_parser("usage", help="Read Colab compute-unit balance and usage rate")
    usage_parser.add_argument("--json", action="store_true", help="Print parsed fields as JSON")

    start_parser = sub.add_parser("start", help="Create a persistent Colab GPU session")
    start_parser.add_argument("--session", required=True)
    start_parser.add_argument("--gpu", default="A100")
    start_parser.add_argument("--no-high-mem", action="store_true")
    start_parser.add_argument("--progress", type=Path)

    stop_parser = sub.add_parser("stop", help="Stop a Colab session")
    stop_parser.add_argument("--session", required=True)
    stop_parser.add_argument("--progress", type=Path)

    batch_parser = sub.add_parser("batch", help="Render multiple videos sequentially on one Colab session")
    batch_parser.add_argument("--manifest", type=Path, required=True)
    batch_parser.add_argument("--session", help="Reuse an existing session instead of creating one")
    batch_parser.add_argument("--gpu", default=os.environ.get("COLAB_GPU", "A100"))
    batch_parser.add_argument("--no-high-mem", action="store_true")
    batch_parser.add_argument("--stop-on-complete", action="store_true", help="Stop even a provided session after this queue")
    batch_parser.add_argument("--progress", type=Path)
    batch_parser.add_argument("--output-dir", type=Path, default=Path("output"))
    batch_parser.add_argument("--timeout", type=float, default=float(os.environ.get("COLAB_EXEC_TIMEOUT", "3600")))

    single_parser = sub.add_parser("single", help="Compatibility interface for run_colab_inference.sh")
    single_parser.add_argument("--image", "-i", action="append", required=True)
    single_parser.add_argument("--prompt", "-p", required=True)
    single_parser.add_argument("--output", "-o")
    single_parser.add_argument("--gpu", default=os.environ.get("COLAB_GPU", "A100"))
    single_parser.add_argument("--no-high-mem", action="store_true")
    single_parser.add_argument("--timeout", type=float, default=float(os.environ.get("COLAB_EXEC_TIMEOUT", "3600")))

    args = parser.parse_args()
    try:
        if args.command == "usage":
            result = get_usage()
            if args.json:
                print(json.dumps({"ok": True, **result}, ensure_ascii=False))
            else:
                print(f"Current balance: {result['balance']:.2f} compute units")
                print(f"Usage rate: {result['rate_per_hour']:.2f}/hr")
                print(f"Active assignments: {result['active_assignments']}")
            return 0
        if args.command == "start":
            start_session(args.session, args.gpu, not args.no_high_mem, args.progress)
            if args.progress is None:
                print(f"Colab session ready: {args.session}")
            return 0
        if args.command == "stop":
            stop_session(args.session, args.progress)
            if args.progress is None:
                print(f"Stopped Colab session: {args.session}")
            return 0
        if args.command == "single":
            first = Path(args.image[0]).expanduser().resolve()
            output = Path(args.output).expanduser().resolve() if args.output else first.with_name(first.stem + "_minimax_h3.mp4")
            manifest = {
                "jobs": [{
                    "title": output.stem,
                    "prompt_file": str(Path(args.prompt).expanduser().resolve()),
                    "reference_images": args.image,
                    "duration_seconds": float(os.environ.get("H3_DURATION_SECONDS", "12")),
                    "output_path": str(output),
                }]
            }
            temp_manifest = output.parent / f".h3_{uuid.uuid4().hex[:10]}.json"
            temp_manifest.parent.mkdir(parents=True, exist_ok=True)
            temp_manifest.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            try:
                progress = run_batch(
                    temp_manifest,
                    session=os.environ.get("COLAB_SESSION_NAME"),
                    gpu=args.gpu,
                    high_mem=not args.no_high_mem,
                    stop_on_complete=True,
                    progress_path=None,
                    output_dir=output.parent,
                    exec_timeout=args.timeout,
                    create_session_if_named=True,
                )
            finally:
                temp_manifest.unlink(missing_ok=True)
            if progress["status"] != "completed":
                print(progress.get("error") or "One or more videos failed.", file=sys.stderr)
                return 1
            print(f"Saved inference output: {output}")
            return 0
        if args.command == "batch":
            progress = run_batch(
                args.manifest.expanduser().resolve(),
                session=args.session,
                gpu=args.gpu,
                high_mem=not args.no_high_mem,
                stop_on_complete=args.stop_on_complete,
                progress_path=args.progress.expanduser().resolve() if args.progress else None,
                output_dir=args.output_dir.expanduser().resolve(),
                exec_timeout=args.timeout,
            )
            print(json.dumps(progress, ensure_ascii=False))
            return 0 if progress["status"] == "completed" else 1
    except Exception as exc:
        if args.command == "usage" and args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
