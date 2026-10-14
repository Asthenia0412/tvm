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

set -euxo pipefail

# Host Python uses wheels in the native pipeline. Keep source mode for old main.
if [ "${TVM_TEST_USE_WHEEL:-0}" = "1" ]; then
    source "$(dirname "$0")/task_python_install_wheels.sh"
else
    export PYTHONPATH="$(pwd)/python"
    uv pip install -v --target=python ./3rdparty/tvm-ffi/
fi

rm -rf .emscripten_cache
cd web
export EM_CACHE=`pwd`/.emscripten_cache
make clean
npm install
npm run lint
npm run prepwasm
npm run bundle
npm run test
npm run typedoc
cd ..
