#!/usr/bin/env bash
# Create an isolated environment for the released CorDriver Stage-2 model.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
project="$(cd "$here/../.." && pwd)"
tools="$(realpath -m "${CORDRIVER_TOOLS:-$project/runs/tools}")"
vision="$tools/cordriver_vision_env"
mm3d="$tools/cordriver_mmdet3d"
commit="5a0ef06cb84156f6fa8510f5bf584403bf91c3dc"
python38="${PYTHON38:-python3.8}"

"$python38" -c 'import sys; assert sys.version_info[:2] == (3,8), sys.version'
: "${CUDA_HOME:?Set CUDA_HOME to the local CUDA toolkit with nvcc}"
test -x "$CUDA_HOME/bin/nvcc"
export CUDA_HOME FORCE_CUDA=1 TORCH_CUDA_ARCH_LIST="${TORCH_CUDA_ARCH_LIST:-8.0}"
export PATH="$CUDA_HOME/bin:$PATH" MAX_JOBS="${MAX_JOBS:-4}"
mkdir -p "$tools"
if [[ ! -e "$tools/cordriver" ]]; then
    git clone https://github.com/Fudan-MAGIC-Lab/Drive-in-Corridors.git "$tools/cordriver"
    git -C "$tools/cordriver" checkout --detach "$commit"
fi
if [[ "$(git -C "$tools/cordriver" rev-parse HEAD)" != "$commit" ]]; then
    echo "CorDriver checkout does not match pinned commit: $commit" >&2
    exit 1
fi
if [[ ! -e "$vision" ]]; then "$python38" -m venv "$vision"; fi
py="$vision/bin/python"
"$py" -c 'import sys; assert sys.version_info[:2] == (3,8)'
"$py" -m pip install 'pip==23.0.1' 'setuptools==56.0.0' 'wheel==0.45.1'
"$py" -m pip install torch==1.9.1+cu111 torchvision==0.10.1+cu111 \
    -f https://download.pytorch.org/whl/torch_stable.html
"$py" -m pip install --only-binary=mmcv-full mmcv-full==1.4.0 \
    -f https://download.openmmlab.com/mmcv/dist/cu111/torch1.9.0/index.html \
    -r "$here/requirements-vision.txt"
if [[ ! -d "$mm3d" ]]; then
    temp="$(mktemp -d)"
    trap 'rm -rf -- "$temp"' EXIT
    archive="$temp/mmdetection3d.tar.gz"
    curl -fsSL https://codeload.github.com/open-mmlab/mmdetection3d/tar.gz/refs/tags/v0.17.1 -o "$archive"
    echo "93c5a53ab92c4889011478c45fb6dbdfc1f25efa714af180991a74df184afbd4  $archive" | sha256sum -c -
    mkdir "$temp/mmdetection3d"
    tar -xzf "$archive" --strip-components=1 -C "$temp/mmdetection3d"
    mv "$temp/mmdetection3d" "$mm3d"
fi
if grep -q '^from numba.errors import NumbaPerformanceWarning$' "$mm3d/mmdet3d/datasets/pipelines/data_augment_utils.py"; then
    patch -p1 -d "$mm3d" < "$here/numba_warning.patch"
fi
export PATH="$vision/bin:$PATH"
(cd "$mm3d" && "$py" setup.py build_ext --inplace)
site_dir="$("$py" -c "import sysconfig; print(sysconfig.get_path('purelib'))")"
printf '%s\n' "$mm3d" > "$site_dir/cordriver_mmdet3d.pth"
(cd "$tools/cordriver" && PYTHONPATH="$PWD" "$py" -B "$here/verify_stack.py")
