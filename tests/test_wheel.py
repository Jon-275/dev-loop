"""The built wheel ships the templates and skills the installed CLI renders from."""

import shutil
import subprocess
import sys
import venv
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv to build the wheel")
def test_installed_wheel_initialises_a_doctor_clean_repo(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    subprocess.run(["uv", "build", "--wheel", "-q", "--out-dir", str(dist), str(ROOT)], check=True)
    (wheel,) = dist.glob("*.whl")
    names = zipfile.ZipFile(wheel).namelist()
    assert "devloop/templates/AGENTS.md" in names
    assert "devloop/skills/devloop-setup/SKILL.md" in names
    env = tmp_path / "env"
    venv.create(env, with_pip=False)
    bin_dir = env / ("Scripts" if sys.platform == "win32" else "bin")
    subprocess.run(
        ["uv", "pip", "install", "-q", "--python", str(bin_dir / "python"), str(wheel)],
        check=True,
    )
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    devloop = str(bin_dir / "devloop")
    subprocess.run([devloop, "init", "--root", str(repo)], check=True, capture_output=True)
    done = subprocess.run(
        [devloop, "doctor", "--check", "--root", str(repo)], capture_output=True, text=True
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "healthy" in done.stdout
