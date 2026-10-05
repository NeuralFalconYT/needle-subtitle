```python
import os
import sys
import shutil
import subprocess
import zipfile
import tempfile
import urllib.request
import time
from pathlib import Path


# ============================================================
# Configuration
# ============================================================

PROJECT_DIR = Path(__file__).resolve().parent

VENV_DIR = PROJECT_DIR / ".venv"
FFMPEG_DIR = PROJECT_DIR / "FFmpeg"
REQUIREMENTS_FILE = PROJECT_DIR / "requirements.txt"
BAT_FILE = PROJECT_DIR / "run_needle_subtitle.bat"

# Gyan FFmpeg Windows build
FFMPEG_URL = (
    "https://www.gyan.dev/ffmpeg/builds/"
    "ffmpeg-release-essentials.zip"
)

FFMPEG_ZIP = PROJECT_DIR / "ffmpeg_download.zip"


# ============================================================
# Helpers
# ============================================================

def print_header(text):
    print()
    print("=" * 70)
    print(text)
    print("=" * 70)


def run_command(command):
    print()
    print(
        ">>>",
        " ".join(
            f'"{x}"' if " " in str(x) else str(x)
            for x in command
        )
    )

    result = subprocess.run(command)

    if result.returncode != 0:
        print()
        print("ERROR: Command failed.")
        sys.exit(result.returncode)


# ============================================================
# Python / venv
# ============================================================

def get_venv_python():

    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"

    return VENV_DIR / "bin" / "python"


def create_virtual_environment():

    print_header("1. Creating Python virtual environment")

    python_exe = get_venv_python()

    if python_exe.exists():

        print("Virtual environment already exists:")
        print(f"  {VENV_DIR}")
        return

    print("Creating .venv ...")

    run_command([
        sys.executable,
        "-m",
        "venv",
        str(VENV_DIR)
    ])

    print("Virtual environment created.")


# ============================================================
# Requirements
# ============================================================

def install_requirements():

    print_header("2. Installing Python dependencies")

    if not REQUIREMENTS_FILE.exists():

        print()
        print("ERROR: requirements.txt was not found.")
        print(f"Expected: {REQUIREMENTS_FILE}")
        sys.exit(1)

    python_exe = get_venv_python()

    if not python_exe.exists():

        print("ERROR: Virtual environment Python was not found.")
        sys.exit(1)

    print("Upgrading pip...")

    run_command([
        str(python_exe),
        "-m",
        "pip",
        "install",
        "--upgrade",
        "pip",
        "setuptools",
        "wheel"
    ])

    print()
    print("Installing requirements...")

    run_command([
        str(python_exe),
        "-m",
        "pip",
        "install",
        "-r",
        str(REQUIREMENTS_FILE)
    ])

    print()
    print("Python dependencies installed successfully.")


# ============================================================
# FFmpeg detection
# ============================================================

def find_local_ffmpeg():

    candidates = [
        FFMPEG_DIR / "bin" / "ffmpeg.exe",
        FFMPEG_DIR / "ffmpeg.exe",
    ]

    for path in candidates:

        if path.exists():
            return path

    if FFMPEG_DIR.exists():

        for path in FFMPEG_DIR.rglob("ffmpeg.exe"):
            return path

    return None


def find_local_ffprobe():

    candidates = [
        FFMPEG_DIR / "bin" / "ffprobe.exe",
        FFMPEG_DIR / "ffprobe.exe",
    ]

    for path in candidates:

        if path.exists():
            return path

    if FFMPEG_DIR.exists():

        for path in FFMPEG_DIR.rglob("ffprobe.exe"):
            return path

    return None


def find_system_ffmpeg():

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")

    if ffmpeg and ffprobe:

        return Path(ffmpeg), Path(ffprobe)

    return None


def verify_ffmpeg(ffmpeg, ffprobe):

    try:

        result = subprocess.run(
            [str(ffmpeg), "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10
        )

        result2 = subprocess.run(
            [str(ffprobe), "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10
        )

        return result.returncode == 0 and result2.returncode == 0

    except Exception:

        return False


# ============================================================
# Fast downloader
# ============================================================

def download_with_curl(url, destination):

    curl = shutil.which("curl")

    if not curl:
        return False

    print()
    print("Using Windows curl downloader...")
    print()
    print(f"URL: {url}")
    print()

    command = [
        curl,
        "-L",
        "--fail",
        "--retry", "5",
        "--retry-delay", "2",
        "--connect-timeout", "20",
        "--continue-at", "-",
        "--progress-bar",
        "-o",
        str(destination),
        url
    ]

    start = time.perf_counter()

    result = subprocess.run(command)

    elapsed = time.perf_counter() - start

    if result.returncode != 0:

        print()
        print("curl download failed.")
        return False

    if destination.exists():

        size_mb = destination.stat().st_size / (1024 * 1024)

        if elapsed > 0:

            speed = size_mb / elapsed

            print()
            print(
                f"Downloaded {size_mb:.1f} MB "
                f"in {elapsed:.1f}s "
                f"({speed:.2f} MB/s)"
            )

        return True

    return False


def download_with_python(url, destination):

    print()
    print("Using Python downloader...")
    print()
    print(f"URL: {url}")
    print()

    start = time.perf_counter()
    last_update = start
    last_downloaded = 0

    def progress(block_num, block_size, total_size):

        nonlocal last_update
        nonlocal last_downloaded

        downloaded = block_num * block_size

        if total_size > 0:

            downloaded = min(downloaded, total_size)

        now = time.perf_counter()

        # Don't redraw too often
        if now - last_update < 0.2:
            return

        elapsed = now - start

        speed = (
            downloaded / elapsed
            if elapsed > 0
            else 0
        )

        if speed > 0 and total_size > 0:

            remaining = total_size - downloaded
            eta = remaining / speed

        else:

            eta = 0

        if total_size > 0:

            percent = downloaded * 100 / total_size

            print(
                f"\r{percent:6.2f}%  "
                f"{downloaded / 1024 / 1024:7.1f} MB / "
                f"{total_size / 1024 / 1024:7.1f} MB  "
                f"{speed / 1024 / 1024:5.2f} MB/s  "
                f"ETA {eta:6.1f}s",
                end="",
                flush=True
            )

        else:

            print(
                f"\r{downloaded / 1024 / 1024:7.1f} MB  "
                f"{speed / 1024 / 1024:5.2f} MB/s",
                end="",
                flush=True
            )

        last_update = now
        last_downloaded = downloaded

    try:

        urllib.request.urlretrieve(
            url,
            destination,
            reporthook=progress
        )

        print()

        elapsed = time.perf_counter() - start

        if destination.exists():

            size_mb = destination.stat().st_size / (
                1024 * 1024
            )

            speed = (
                size_mb / elapsed
                if elapsed > 0
                else 0
            )

            print(
                f"Downloaded {size_mb:.1f} MB "
                f"in {elapsed:.1f}s "
                f"({speed:.2f} MB/s)"
            )

        return True

    except Exception as e:

        print()
        print(f"Download failed: {e}")

        return False


def download_ffmpeg():

    print()
    print("Downloading FFmpeg...")
    print()

    # Reuse an existing complete download
    if FFMPEG_ZIP.exists():

        size_mb = FFMPEG_ZIP.stat().st_size / (
            1024 * 1024
        )

        print(
            f"Found existing download: "
            f"{size_mb:.1f} MB"
        )

        print("Reusing it instead of downloading again.")

        return True

    # Prefer Windows curl
    if download_with_curl(
        FFMPEG_URL,
        FFMPEG_ZIP
    ):

        return True

    # Fallback
    print()
    print("Falling back to Python downloader...")

    return download_with_python(
        FFMPEG_URL,
        FFMPEG_ZIP
    )


# ============================================================
# FFmpeg installation
# ============================================================

def install_ffmpeg():

    print_header("3. Checking FFmpeg")

    # --------------------------------------------------------
    # 1. Local project FFmpeg
    # --------------------------------------------------------

    local_ffmpeg = find_local_ffmpeg()
    local_ffprobe = find_local_ffprobe()

    if local_ffmpeg and local_ffprobe:

        if verify_ffmpeg(
            local_ffmpeg,
            local_ffprobe
        ):

            print("FFmpeg already installed locally.")

            print(f"  ffmpeg : {local_ffmpeg}")
            print(f"  ffprobe: {local_ffprobe}")

            return local_ffmpeg

    # --------------------------------------------------------
    # 2. System FFmpeg
    # --------------------------------------------------------

    system = find_system_ffmpeg()

    if system:

        ffmpeg, ffprobe = system

        print("FFmpeg already available in Windows PATH.")

        print(f"  ffmpeg : {ffmpeg}")
        print(f"  ffprobe: {ffprobe}")

        return ffmpeg

    # --------------------------------------------------------
    # 3. Download
    # --------------------------------------------------------

    print("FFmpeg was not found.")

    if not download_ffmpeg():

        print()
        print("ERROR: Could not download FFmpeg.")
        sys.exit(1)

    # --------------------------------------------------------
    # 4. Extract
    # --------------------------------------------------------

    print()
    print("Extracting FFmpeg...")

    temp_extract = PROJECT_DIR / "_ffmpeg_extract"

    if temp_extract.exists():

        shutil.rmtree(temp_extract)

    temp_extract.mkdir(
        parents=True,
        exist_ok=True
    )

    try:

        with zipfile.ZipFile(
            FFMPEG_ZIP,
            "r"
        ) as zip_ref:

            zip_ref.extractall(temp_extract)

    except zipfile.BadZipFile:

        print()
        print("ERROR: FFmpeg ZIP is corrupted.")
        print()
        print("Delete:")
        print(f"  {FFMPEG_ZIP}")

        sys.exit(1)

    # --------------------------------------------------------
    # Find executable
    # --------------------------------------------------------

    ffmpeg_exe = None
    ffprobe_exe = None

    for path in temp_extract.rglob(
        "ffmpeg.exe"
    ):

        ffmpeg_exe = path
        break

    for path in temp_extract.rglob(
        "ffprobe.exe"
    ):

        ffprobe_exe = path
        break

    if not ffmpeg_exe or not ffprobe_exe:

        print()
        print(
            "ERROR: ffmpeg.exe or "
            "ffprobe.exe not found."
        )

        sys.exit(1)

    extracted_root = ffmpeg_exe.parent.parent

    # --------------------------------------------------------
    # Install
    # --------------------------------------------------------

    if FFMPEG_DIR.exists():

        shutil.rmtree(
            FFMPEG_DIR
        )

    shutil.copytree(
        extracted_root,
        FFMPEG_DIR
    )

    # Cleanup extraction
    shutil.rmtree(
        temp_extract,
        ignore_errors=True
    )

    # --------------------------------------------------------
    # Verify
    # --------------------------------------------------------

    final_ffmpeg = find_local_ffmpeg()
    final_ffprobe = find_local_ffprobe()

    if not final_ffmpeg or not final_ffprobe:

        print()
        print("ERROR: FFmpeg installation failed.")
        sys.exit(1)

    if not verify_ffmpeg(
        final_ffmpeg,
        final_ffprobe
    ):

        print()
        print("ERROR: FFmpeg verification failed.")
        sys.exit(1)

    print()
    print("FFmpeg installed successfully.")

    print(f"  ffmpeg : {final_ffmpeg}")
    print(f"  ffprobe: {final_ffprobe}")

    # --------------------------------------------------------
    # Keep ZIP as cache
    # --------------------------------------------------------

    print()
    print("FFmpeg ZIP kept as local cache.")

    return final_ffmpeg


# ============================================================
# BAT launcher
# ============================================================

def create_launcher():

    print_header("4. Creating launcher")

    python_exe = get_venv_python()
    app_file = PROJECT_DIR / "app.py"

    if not app_file.exists():

        print()
        print("WARNING: app.py was not found.")
        print(f"Expected: {app_file}")
        print()

    ffmpeg_exe = find_local_ffmpeg()

    if ffmpeg_exe is None:

        system = find_system_ffmpeg()

        if system:
            ffmpeg_exe = system[0]

    if ffmpeg_exe is None:

        print("ERROR: FFmpeg executable not found.")
        sys.exit(1)

    ffmpeg_bin = ffmpeg_exe.parent

    project_path = str(PROJECT_DIR)
    python_path = str(python_exe)
    app_path = str(app_file)
    ffmpeg_bin_path = str(ffmpeg_bin)

    bat_content = f"""@echo off
setlocal

title Needle Subtitle

echo.
echo ============================================================
echo              Needle Subtitle
echo ============================================================
echo.

cd /d "{project_path}"

set "PATH={ffmpeg_bin_path};%PATH%"

echo Project:
echo   {project_path}
echo.

echo Starting application...
echo.

"{python_path}" "{app_path}"

echo.
echo ============================================================
echo Application closed.
echo ============================================================
echo.

pause
"""

    BAT_FILE.write_text(
        bat_content,
        encoding="utf-8"
    )

    print()
    print("Launcher created:")
    print(f"  {BAT_FILE}")


# ============================================================
# Main
# ============================================================

def main():

    print()
    print("=" * 70)
    print("        Needle Subtitle - Windows Installer")
    print("=" * 70)
    print()

    print("Project directory:")
    print(f"  {PROJECT_DIR}")
    print()

    if os.name != "nt":

        print(
            "WARNING: This installer is designed "
            "for Windows."
        )

    # 1
    create_virtual_environment()

    # 2
    install_requirements()

    # 3
    install_ffmpeg()

    # 4
    create_launcher()

    print_header(
        "INSTALLATION COMPLETE"
    )

    print("Your project is ready!")
    print()

    print("Created / available:")
    print("  .venv/")
    print("  FFmpeg/")
    print("  run_needle_subtitle.bat")
    print()

    print("Start the application with:")
    print()
    print("  run_needle_subtitle.bat")
    print()

    print("You do NOT need to:")
    print("  - activate the virtual environment")
    print("  - install FFmpeg manually")
    print("  - add FFmpeg to Windows PATH")
    print("  - manually install Python packages")
    print()


if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print()
        print()
        print("Installation cancelled by user.")
        sys.exit(1)

    except Exception as e:

        print()
        print("=" * 70)
        print("INSTALLATION FAILED")
        print("=" * 70)
        print()
        print(f"Error: {e}")
        print()

        input(
            "Press Enter to exit..."
        )

        sys.exit(1)
```
