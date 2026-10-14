#!/usr/bin/env bash
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

# Source this file so subsequent Python invocations use the wheel environment.
# Docker defaults PYTHONPATH to the checkout; never let it shadow the artifacts.
unset PYTHONPATH TVM_LIBRARY_PATH TVM_FFI_LIBRARY_PATH
export PYTHONNOUSERSITE=1
TVM_WHEEL_DIR="$(realpath "${TVM_WHEEL_DIR:-$(pwd)/build/wheels}")"
# Preserve the image's active environment (often itself a venv), including when
# local ci.py sources this setup again for a named test suite.
export TVM_IMAGE_PYTHON="${TVM_IMAGE_PYTHON:-$(command -v python3)}"
export TVM_IMAGE_SITE_PACKAGES="${TVM_IMAGE_SITE_PACKAGES:-$("$TVM_IMAGE_PYTHON" -I -c 'import site; print("\n".join(site.getsitepackages()))')}"
export VIRTUAL_ENV="${TVM_WHEEL_DIR%/}/../wheel-venv"
uv venv --offline --python "$TVM_IMAGE_PYTHON" --clear "$VIRTUAL_ENV"
# Append the image dependencies after our wheels without executing its .pth files:
# system-site-packages can install editable-import hooks ahead of the wheel.
TVM_WHEEL_SITE="$("$VIRTUAL_ENV/bin/python3" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
printf '%s\n' "$TVM_IMAGE_SITE_PACKAGES" > "$TVM_WHEEL_SITE/image-dependencies.pth"
export PATH="$VIRTUAL_ENV/bin:$PATH"
uv pip install --python "$VIRTUAL_ENV/bin/python3" --no-deps --offline --reinstall \
    "$TVM_WHEEL_DIR"/apache_tvm-*.whl

# Fail before pytest if a source/editable install or native override leaked in.
python3 - <<'PYTHON'
import os
import sys
from importlib.metadata import requires, version
from pathlib import Path
from packaging.requirements import Requirement

# --no-deps leaves dependency ownership with the image. Check the wheel's own
# requirements without invoking a resolver or accepting a stale hardcoded floor.
for text in requires("apache-tvm") or []:
    requirement = Requirement(text)
    if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
        continue
    installed = version(requirement.name)
    assert requirement.specifier.contains(installed, prereleases=True), (text, installed)

import tvm
import tvm_ffi

prefix = Path(sys.prefix).resolve()
path = Path(tvm.__file__).resolve()
assert path.is_relative_to(prefix), path
print(f"Installed tvm: {path}")
# FFI is supplied by the image, not rebuilt or transferred with TVM.
image_sites = [Path(p).resolve() for p in os.environ["TVM_IMAGE_SITE_PACKAGES"].splitlines()]
for path in (Path(tvm_ffi.__file__).resolve(), Path(tvm_ffi.LIB._name).resolve()):
    assert any(path.is_relative_to(site) for site in image_sites), path
    print(f"Image FFI: {path}")
assert {"tvm_runtime", "tvm_compiler"} <= tvm.base._LOADED_LIBS.keys()
for name, library in tvm.base._LOADED_LIBS.items():
    path = Path(library._name).resolve()
    assert path.is_relative_to(prefix), (name, path)
    print(f"Installed {name}: {path}")
PYTHON
