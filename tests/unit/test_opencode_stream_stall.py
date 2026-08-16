"""A stalled agent stream must classify as an infra timeout, never as a
truncated-but-accepted outcome. Regression for run 01KZTHE7 T-017: the
inactivity kill left partial JSON events on stdout that _check_json's
lenient signal branch accepted, burning agent attempts on impl_defect
loops; _stream_stdout must therefore report the stall so _run can raise
OpencodeError("timeout") (an infra class: no attempt consumed)."""

import subprocess
import sys

from tracks.effects.opencode import OpencodeBackend


def test_stream_stall_reports_timeout(tmp_path):
    backend = OpencodeBackend(tmp_path, "v0.1")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time; sys.stdout.write('{\"type\": \"step_finish\"}\\n');"
            "sys.stdout.flush(); time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        chunks, manifest_found, stalled = backend._stream_stdout(proc, 0.5)
        assert stalled is True
        assert manifest_found is False
        assert chunks  # partial events retained as failure evidence
    finally:
        proc.kill()
        proc.wait()


def test_stream_clean_exit_no_stall(tmp_path):
    backend = OpencodeBackend(tmp_path, "v0.1")
    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdout.write('{\"type\": \"step_finish\"}\\n')"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    chunks, manifest_found, stalled = backend._stream_stdout(proc, 10.0)
    assert stalled is False
    assert manifest_found is False
    assert chunks
