"""Make the pip-installed NVIDIA DLLs (cuBLAS, cuDNN) findable on Windows. Import before ctranslate2/onnxruntime."""
import os
import sys
from pathlib import Path


def add_nvidia_dll_dirs() -> list[str]:
    """Prepend site-packages/nvidia/*/bin to PATH (ctranslate2 loads cuDNN lazily by name, so PATH is what counts)."""
    root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    # Several CUDA generations can live side by side: ctranslate2 wants CUDA 12
    # (cublas64_12.dll in nvidia/cublas/bin) while newer runtimes use
    # nvidia/cu13/bin/x86_64. The file names differ, so both fit on PATH.
    dirs = [str(p) for p in list(root.glob("*/bin")) + list(root.glob("*/bin/x86_64")) if p.is_dir()]
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs + [os.environ.get("PATH", "")])
        for d in dirs:
            os.add_dll_directory(d)
    return dirs
