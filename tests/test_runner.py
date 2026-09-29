from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import runner


FAKE_COLAB = r'''#!PYTHON_EXECUTABLE
import json, os, pathlib, shutil, sys
args = sys.argv[1:]
if args and args[0].startswith("--auth="):
    args = args[1:]
command = args[0]
args = args[1:]
root = pathlib.Path(os.environ["FAKE_COLAB_STATE_DIR"])
root.mkdir(parents=True, exist_ok=True)
log = root / "calls.jsonl"
def record(kind, **values):
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"command": kind, **values}) + "\n")
if command == "usage":
    print("Current balance: 100.00 compute units")
    print("Usage rate: 0.00/hr")
    print("Active assignments: 0")
elif command == "new":
    record("new", args=args)
    print("Session created")
elif command == "stop":
    record("stop", args=args)
    print("Session stopped")
elif command == "upload":
    remote = args[-1]
    source = pathlib.Path(args[-2])
    remote_path = root / "remote" / remote.lstrip("/")
    remote_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, remote_path)
    record("upload", remote=remote, source=source.name)
elif command == "exec":
    envs = [args[i + 1] for i, value in enumerate(args[:-1]) if value == "--env"]
    values = dict(item.split("=", 1) for item in envs)
    output = values["H3_OUTPUT_PATH"]
    record("exec", output=output, refs=json.loads(values["H3_REFERENCE_IMAGES"]), prompt=values["H3_PROMPT_FILE"])
    if values.get("H3_OUTPUT_PREFIX") == os.environ.get("FAKE_FAIL_PREFIX"):
        print("simulated inference failure", file=sys.stderr)
        sys.exit(7)
    remote_path = root / "remote" / output.lstrip("/")
    remote_path.parent.mkdir(parents=True, exist_ok=True)
    remote_path.write_bytes(b"fake video bytes")
elif command == "download":
    remote = args[-2]
    target = pathlib.Path(args[-1])
    source = root / "remote" / remote.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    record("download", remote=remote, target=target.name)
else:
    print("unexpected fake command: " + command, file=sys.stderr)
    sys.exit(2)
'''

FAKE_FFPROBE = r'''#!PYTHON_EXECUTABLE
print('{"streams":[{"codec_type":"video"},{"codec_type":"audio"}]}')
'''


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        for name, content in (("colab", FAKE_COLAB), ("ffprobe", FAKE_FFPROBE)):
            path = self.fake_bin / name
            path.write_text(content.replace("PYTHON_EXECUTABLE", sys.executable), encoding="utf-8")
            path.chmod(0o755)
            if sys.platform == "win32":
                bat = self.fake_bin / f"{name}.bat"
                bat.write_text(f'@"{sys.executable}" "{path}" %*\n', encoding="utf-8")
        self.state_dir = self.root / "state"
        fake_path = os.pathsep.join([str(self.fake_bin), str(Path(sys.executable).parent), os.environ.get("PATH", "")])
        self.env_patch = patch.dict(os.environ, {
            "PATH": fake_path,
            "FAKE_COLAB_STATE_DIR": str(self.state_dir),
        })
        self.env_patch.start()

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.temp.cleanup()

    def manifest(self, job_count: int = 2, prompt_file: bool = False) -> Path:
        jobs = []
        for index in range(job_count):
            image = self.root / f"reference-{index}.png"
            image.write_bytes(b"fake image")
            job = {
                "id": f"job-{index}",
                "title": f"clip {index}",
                "reference_images": [str(image)],
                "prompt": f"complete prompt <Picture 1> for clip {index}",
                "duration_seconds": 8,
                "seed": 100 + index,
                "output_name": f"clip-{index}",
            }
            if prompt_file:
                prompt = self.root / f"prompt-{index}.txt"
                prompt.write_text(job.pop("prompt"), encoding="utf-8")
                job["prompt_file"] = str(prompt)
            jobs.append(job)
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
        return manifest

    def calls(self) -> list[dict[str, object]]:
        path = self.state_dir / "calls.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_usage_parser_handles_labelled_colab_cli_output(self) -> None:
        result = runner.parse_usage("Current balance: 1,234.50 compute units\nUsage rate: 12.00/hr\nActive assignments: 3\n")
        self.assertEqual(result["balance"], 1234.5)
        self.assertEqual(result["active_assignments"], 3)

    def test_usage_command_reads_colab_through_cli(self) -> None:
        result = runner.get_usage()
        self.assertEqual(result["balance"], 100.0)
        self.assertEqual(result["rate_per_hour"], 0.0)
        self.assertEqual(result["active_assignments"], 0)

    def test_guided_prompt_builds_shots_and_checks_picture_mapping(self) -> None:
        prompt = runner.compose_ref2va_prompt(
            subject_definitions="<Subject 1> is the presenter in <Picture 1>.",
            summary="A short introduction.",
            retention_analysis="<Subject 1> remains consistent across [Shot 1].",
            shots=[
                {"start_seconds": 0, "description": "Faces the camera.", "dialogue": "大家好！"},
                {"start_seconds": 4.2, "description": "Gestures toward a terminal card."},
            ],
            overall_soundscape="Quiet studio ambience.",
            non_diegetic_music="N/A",
            image_count=1,
            duration_seconds=8,
            detailed_description="Opening: a clean blue studio with a terminal card beside the presenter.",
        )
        self.assertIn("<d>[Chinese] 大家好！</d>", prompt)
        self.assertIn("[Shot 2] At 00:04.200", prompt)
        self.assertIn("Opening: a clean blue studio", prompt)
        with self.assertRaisesRegex(ValueError, "Picture"):
            runner.compose_ref2va_prompt(
                subject_definitions="<Subject 1> uses <Picture 2>.",
                summary="Summary", retention_analysis="Retention",
                shots=[{"start_seconds": 0, "description": "Faces camera."}],
                overall_soundscape="Room tone", non_diegetic_music="N/A",
                image_count=1, duration_seconds=8,
            )

    def test_batch_reuses_one_session_and_preserves_prompt_file_content(self) -> None:
        manifest = self.manifest(2, prompt_file=True)
        state_path = self.root / "progress.json"
        result = runner.run_batch(
            manifest, session=None, gpu="A100", high_mem=True, stop_on_complete=False,
            progress_path=state_path, output_dir=self.root / "outputs", exec_timeout=100,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["completed_count"], 2)
        self.assertEqual(result["session_status"], "stopped")
        self.assertTrue(all(Path(item["output"]).is_file() for item in result["results"]))
        calls = self.calls()
        self.assertEqual(sum(call["command"] == "new" for call in calls), 1)
        self.assertEqual(sum(call["command"] == "exec" for call in calls), 2)
        self.assertEqual(sum(call["command"] == "stop" for call in calls), 1)
        uploads = [call for call in calls if call["command"] == "upload"]
        self.assertEqual(len({call["remote"] for call in uploads}), 4)

    def test_failed_later_job_keeps_completed_video_and_stops_session(self) -> None:
        manifest = self.manifest(2)
        with patch.dict(os.environ, {"FAKE_FAIL_PREFIX": "MiniMax_H3_job-1"}):
            result = runner.run_batch(
                manifest, session=None, gpu="A100", high_mem=False, stop_on_complete=False,
                progress_path=None, output_dir=self.root / "outputs", exec_timeout=100,
            )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["completed_count"], 1)
        self.assertEqual(result["failed_count"], 1)
        self.assertTrue(Path(result["results"][0]["output"]).is_file())
        self.assertEqual(sum(call["command"] == "stop" for call in self.calls()), 1)

    def test_shell_launcher_accepts_multiple_local_images_and_prompt_file(self) -> None:
        image_one = self.root / "ref-one.png"
        image_two = self.root / "ref-two.jpg"
        prompt = self.root / "prompt.txt"
        output = self.root / "from-shell.mp4"
        image_one.write_bytes(b"fake png bytes")
        image_two.write_bytes(b"fake jpeg bytes")
        prompt_content = "complete prompt with <Picture 1> and <Picture 2>\n"
        prompt.write_text(prompt_content, encoding="utf-8")
        project = Path(__file__).resolve().parents[1]
        cmd = [
            str(project / "run_colab_inference.sh"),
            "--image", str(image_one), "--image", str(image_two),
            "--prompt", str(prompt), "--output", str(output),
        ]
        if sys.platform == "win32":
            bash = shutil.which("bash")
            if bash:
                cmd = [bash, *cmd]
            else:
                cmd = [sys.executable, str(project / "scripts" / "runner.py"), "single", *cmd[1:]]
        subprocess.run(cmd, env=os.environ.copy(), capture_output=True, text=True, check=True, timeout=30)
        self.assertEqual(output.read_bytes(), b"fake video bytes")
        calls = self.calls()
        self.assertEqual(sum(call["command"] == "new" for call in calls), 1)
        self.assertEqual(sum(call["command"] == "exec" for call in calls), 1)
        self.assertEqual(sum(call["command"] == "stop" for call in calls), 1)
        exec_call = next(call for call in calls if call["command"] == "exec")
        self.assertEqual(len(exec_call["refs"]), 2)
        uploaded_prompt = self.state_dir / "remote" / str(exec_call["prompt"]).lstrip("/")
        self.assertEqual(uploaded_prompt.read_text(encoding="utf-8"), prompt_content)

    def test_resolve_jobs_rejects_duplicate_job_ids_and_outputs(self) -> None:
        manifest = self.manifest(2)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["jobs"][1]["id"] = data["jobs"][0]["id"]
        with self.assertRaisesRegex(ValueError, "duplicates job id"):
            runner.resolve_jobs(data, self.root / "output")
        data["jobs"][1]["id"] = "job-1"
        data["jobs"][1]["output_path"] = str(self.root / "same.mp4")
        data["jobs"][0]["output_path"] = str(self.root / "same.mp4")
        with self.assertRaisesRegex(ValueError, "same output path"):
            runner.resolve_jobs(data, self.root / "output")

    def test_resolve_jobs_rejects_picture_tags_outside_uploaded_references(self) -> None:
        manifest = self.manifest(1)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["jobs"][0]["prompt"] += " <Picture 2>"
        manifest.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Picture"):
            runner.resolve_jobs(data, self.root / "output")


if __name__ == "__main__":
    unittest.main()
