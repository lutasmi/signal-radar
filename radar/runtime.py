import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENV_DIR = PROJECT_ROOT / ".venv"
VENV_PYTHON = VENV_DIR / "bin" / "python"
REQUIREMENTS_FILE = PROJECT_ROOT / "requirements.txt"
REQUIRED_PYTHON_MAJOR = 3
REQUIRED_PYTHON_MINOR = 11


def modules_available(module_names):
    return all(importlib.util.find_spec(module_name) for module_name in module_names)


def running_inside_project_venv():
    return Path(sys.prefix).resolve() == VENV_DIR.resolve()


def python_version(python):
    result = subprocess.run(
        [
            str(python),
            "-c",
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def required_python_version():
    return f"{REQUIRED_PYTHON_MAJOR}.{REQUIRED_PYTHON_MINOR}"


def venv_uses_required_python():
    if not VENV_PYTHON.exists():
        return False
    return python_version(VENV_PYTHON) == required_python_version()


def venv_modules_available(module_names):
    if not VENV_PYTHON.exists():
        return False

    import_lines = "; ".join(f"import {module_name}" for module_name in module_names)
    result = subprocess.run(
        [str(VENV_PYTHON), "-c", import_lines],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def find_required_python():
    candidates = [
        shutil.which("python3.11"),
        shutil.which("python3"),
        sys.executable,
    ]
    for candidate in candidates:
        if candidate and python_version(candidate) == required_python_version():
            return candidate
    raise RuntimeError(
        f"Python {required_python_version()} is required to create {VENV_DIR}"
    )


def create_venv():
    python = find_required_python()
    args = [python, "-m", "venv", str(VENV_DIR)]
    if VENV_DIR.exists():
        args.insert(3, "--clear")
    subprocess.run(
        args,
        cwd=PROJECT_ROOT,
        check=True,
    )


def install_requirements():
    subprocess.run(
        [str(VENV_PYTHON), "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)],
        cwd=PROJECT_ROOT,
        check=True,
    )


def ensure_project_runtime(required_modules):
    if os.getenv("SIGNAL_RADAR_SKIP_VENV") == "1":
        return

    if modules_available(required_modules):
        return

    if running_inside_project_venv():
        install_requirements()
        return

    if not venv_uses_required_python():
        create_venv()

    if not venv_modules_available(required_modules):
        install_requirements()

    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])
