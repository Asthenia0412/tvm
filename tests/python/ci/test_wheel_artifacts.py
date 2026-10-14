# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""Wheel artifact transport and local CI command contracts, without a TVM import."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load_local_ci():
    spec = importlib.util.spec_from_file_location("local_ci", ROOT / "tests/scripts/ci.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("platform", ["cpu", "gpu", "arm", "wasm"])
def test_local_wheel_build(monkeypatch, platform):
    ci = load_local_ci()
    calls = []
    monkeypatch.setattr(ci, "docker", lambda **kwargs: calls.append(kwargs))
    command, _, _ = ci.generate_command(platform, {}, "wheel build")
    command(cpp=True)
    scripts = calls[0]["scripts"]
    wheel = next(script for script in scripts if "-Cbuild-dir=" in script)
    assert "-Ccmake.build-type=RelWithDebInfo" in wheel
    assert "all;cpptest" in wheel
    assert any("./3rdparty/tvm-ffi" in script for script in scripts)
    assert any("task_python_install_wheels.sh" in script for script in scripts) == (
        platform != "wasm"
    )
    calls.clear()
    command(skip_build=True, tests=["tests/python/example.py"])
    assert not any("uv build" in script for script in calls[0]["scripts"])


@pytest.mark.parametrize("succeed", [True, False])
def test_wheel_directory_upload_retries_each_transfer(tmp_path, succeed):
    # Exercise the existing S3 CLI/retry boundary with a directory bundle.
    for relative in [
        "ci/scripts/jenkins/s3.py",
        "ci/scripts/jenkins/cmd_utils.py",
        "ci/scripts/jenkins/retry.sh",
        "ci/jenkins/data.py",
    ]:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / relative, target)
    wheels = tmp_path / "build/wheels"
    wheels.mkdir(parents=True)
    (wheels / "apache_tvm.whl").write_text("tvm")
    (wheels / "apache_tvm_ffi.whl").write_text("ffi")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    aws = binaries / "aws"
    aws.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        "path = pathlib.Path('attempts.json')\n"
        "calls = json.loads(path.read_text()) if path.exists() else []\n"
        "calls.append(sys.argv[1:])\n"
        "path.write_text(json.dumps(calls))\n"
        f"sys.exit(0 if {succeed!r} and len(calls) == 3 else 1)\n"
    )
    aws.chmod(0o755)
    sleep = binaries / "sleep"
    sleep.write_text('#!/bin/sh\necho "$1" >> delays.txt\n')
    sleep.chmod(0o755)
    result = subprocess.run(
        [
            sys.executable,
            "ci/scripts/jenkins/s3.py",
            "--action",
            "upload",
            "--bucket",
            "test",
            "--prefix",
            "cpu",
            "--bundle",
            "wheels",
        ],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) == succeed, result.stdout + result.stderr
    attempts = json.loads((tmp_path / "attempts.json").read_text())
    assert len(attempts) == 3
    assert all(
        call[-3:] == ["--recursive", "build/wheels", "s3://test/cpu/build/wheels"]
        for call in attempts
    )
    delays = [int(line) for line in (tmp_path / "delays.txt").read_text().splitlines()]
    assert len(delays) == 2 and all(30 <= delay <= 200 for delay in delays)


@pytest.mark.parametrize("fail", [False, True])
def test_local_interactive_keeps_cache_stats(tmp_path, monkeypatch, fail):
    ci = load_local_ci()
    monkeypatch.setattr(ci, "check_docker", lambda: None)
    monkeypatch.setattr(ci, "cmd", lambda *args, **kwargs: None)
    monkeypatch.setattr(ci, "SCRIPT_DIR", tmp_path / "scripts")
    monkeypatch.setattr(ci, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("USE_SCCACHE", raising=False)
    monkeypatch.setenv("DEBUG", "1")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name in ["sccache", "bash"]:
        binary = binaries / name
        binary.write_text(f'#!/bin/sh\necho "{name} $*" >> "$CALL_LOG"\n')
        binary.chmod(0o755)
    ci.docker("interactive", "ci_cpu", ["false" if fail else "true"], {}, True)
    result = subprocess.run(
        [shutil.which("bash"), str(ci.SCRIPT_DIR / "interactive.sh")],
        env={
            **os.environ,
            "PATH": f"{binaries}:{os.environ['PATH']}",
            "CALL_LOG": str(tmp_path / "calls"),
        },
        capture_output=True,
        text=True,
    )
    assert (result.returncode != 0) == fail
    calls = (tmp_path / "calls").read_text().splitlines()
    assert calls == [
        "sccache --start-server",
        "sccache --show-stats",
        "sccache --show-stats",
        "bash ",
    ]


@pytest.mark.parametrize("wheel_docs", [False, True])
def test_docs_import_path(tmp_path, monkeypatch, wheel_docs):
    # Run the real Sphinx path bootstrap without requiring Sphinx extensions.
    # A stale --target=python FFI package must not precede Jenkins' wheel install.
    monkeypatch.chdir(ROOT / "docs")
    source_python = str(ROOT / "python")
    monkeypatch.setattr(sys, "path", [path for path in sys.path if path != source_python])
    monkeypatch.setenv("TVM_DOCS_USE_WHEEL", "1" if wheel_docs else "0")
    config = ROOT / "docs/conf.py"
    bootstrap = config.read_text().split("# -- General configuration")[0]
    exec(compile(bootstrap, str(config), "exec"), {"__file__": str(config)})
    assert (source_python in sys.path) != wheel_docs
