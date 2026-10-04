import os
import sys
import shutil
import subprocess
import urllib.request
import zipfile
import tempfile
from pathlib import Path


# ============================================================
# Configuration
# ============================================================

PROJECT_DIR = Path(__file__).resolve().parent

VENV_DIR = PROJECT_DIR / ".venv"
FFMPEG_DIR = PROJECT_DIR / "FFmpeg"
REQUIREMENTS_FILE = PROJECT_DIR / "requirements.txt"
BAT_FILE = PROJECT_DIR / "run_needle_subtitle.bat"

FFMPEG_URL = (
    "https://www.gyan.dev/ffmpeg/builds/"
    "ffmpeg-release-essentials.zip"
)


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
    print(">>>", " ".join(f'"{x}"' if " " in str(x) else str(x)
                         for x in command))

    result = subprocess.run(command)

    if result.returncode != 0:
        print()
        print("ERROR: Command failed.")
        sys.exit(result.returncode)


def download_file(url, destination):
    print()
    print("Downloading FFmpeg...")
    print(f"URL: {url}")
    print()

    urllib.request.urlretrieve(
        url,
        destination,
        reporthook=download_progress
    )

    print()
    print("Download complete.")


def download_progress(block_num, block_size, total_size):
    if total_size <= 0:
        return

    downloaded = block_num * block_size
    percent = min(downloaded * 100 / total_size, 100)

    print(
        f"\rDownloading: {percent:6.2f}%",
        end="",
        flush=True
    )


# ============================================================
# Python / venv
# ============================================================

def get_venv_python():
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"

    return VENV_DIR / "bin" / "python"


def create_virtual_environment():
    print_header("1. Creating Python virtual environment")

    if VENV_DIR.exists():
        print(f"Virtual environment already exists:")
        print(f"  {VENV_DIR}")
    else:
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
        print()
        print(f"Expected:")
        print(f"  {REQUIREMENTS_FILE}")
        print()
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
    print("Installing everything from:")
    print(f"  {REQUIREMENTS_FILE}")

    run_command([
        str(python_exe),
        "-m",
        "pip",
        "install",
        "-r",
        str(REQUIREMENTS_FILE)
    ])

    print()
    print("All requirements installed successfully.")


# ============================================================
# FFmpeg
# ============================================================

def find_ffmpeg_executable():
    """
    Look for ffmpeg.exe inside our local FFmpeg folder.
    """

    possible = [
        FFMPEG_DIR / "bin" / "ffmpeg.exe",
        FFMPEG_DIR / "ffmpeg.exe",
    ]

    for path in possible:
        if path.exists():
            return path

    # Also search recursively in case archive structure changes
    for path in FFMPEG_DIR.rglob("ffmpeg.exe"):
        return path

    return None


def install_ffmpeg():
    print_header("3. Installing FFmpeg")

    existing_ffmpeg = find_ffmpeg_executable()

    if existing_ffmpeg:
        print("FFmpeg is already installed.")
        print(f"  {existing_ffmpeg}")
        return existing_ffmpeg

    FFMPEG_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temp_dir:

        temp_dir = Path(temp_dir)

        zip_file = temp_dir / "ffmpeg.zip"
        extract_dir = temp_dir / "extracted"

        # ----------------------------------------------------
        # Download
        # ----------------------------------------------------

        download_file(
            FFMPEG_URL,
            zip_file
        )

        # ----------------------------------------------------
        # Extract
        # ----------------------------------------------------

        print()
        print("Extracting FFmpeg...")

        with zipfile.ZipFile(zip_file, "r") as zip_ref:
            zip_ref.extractall(extract_dir)

        # ----------------------------------------------------
        # Find extracted FFmpeg folder
        # ----------------------------------------------------

        ffmpeg_exe = None

        for path in extract_dir.rglob("ffmpeg.exe"):
            ffmpeg_exe = path
            break

        if ffmpeg_exe is None:
            print()
            print("ERROR: Could not find ffmpeg.exe in downloaded archive.")
            sys.exit(1)

        extracted_root = ffmpeg_exe.parent.parent

        # ----------------------------------------------------
        # Remove old incomplete FFmpeg installation
        # ----------------------------------------------------

        if FFMPEG_DIR.exists():
            shutil.rmtree(FFMPEG_DIR)

        # ----------------------------------------------------
        # Copy complete FFmpeg build
        # ----------------------------------------------------

        shutil.copytree(
            extracted_root,
            FFMPEG_DIR
        )

    # --------------------------------------------------------
    # Verify
    # --------------------------------------------------------

    final_ffmpeg = find_ffmpeg_executable()

    if final_ffmpeg is None:
        print()
        print("ERROR: FFmpeg installation failed.")
        sys.exit(1)

    print()
    print("FFmpeg installed successfully:")
    print(f"  {final_ffmpeg}")

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
        print("The launcher will still be created.")

    ffmpeg_exe = find_ffmpeg_executable()

    if ffmpeg_exe is None:
        print("ERROR: FFmpeg executable not found.")
        sys.exit(1)

    ffmpeg_bin = ffmpeg_exe.parent

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # We intentionally use absolute paths here.
    #
    # Therefore the BAT file can be moved anywhere.
    # --------------------------------------------------------

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
    print()
    print("The launcher uses absolute paths.")
    print("You can move the BAT file anywhere.")


# ============================================================
# Main
# ============================================================

def main():

    print()
    print("============================================================")
    print("        Needle Subtitle - Windows Installer")
    print("============================================================")
    print()
    print(f"Project directory:")
    print(f"  {PROJECT_DIR}")
    print()

    if os.name != "nt":
        print("WARNING: This installer is designed for Windows.")
        print()

    # 1. Create venv
    create_virtual_environment()

    # 2. Install requirements.txt
    install_requirements()

    # 3. Install FFmpeg
    install_ffmpeg()

    # 4. Create movable BAT launcher
    create_launcher()

    # --------------------------------------------------------
    # Finished
    # --------------------------------------------------------

    print_header("INSTALLATION COMPLETE")

    print("Your project is ready!")
    print()
    print("Created:")
    print(f"  .venv/")
    print(f"  FFmpeg/")
    print(f"  run_needle_subtitle.bat")
    print()
    print("To start Needle Subtitle:")
    print()
    print("  Double-click:")
    print(f"  {BAT_FILE.name}")
    print()
    print("The BAT file can be moved anywhere.")
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
        input("Press Enter to exit...")
        sys.exit(1)
