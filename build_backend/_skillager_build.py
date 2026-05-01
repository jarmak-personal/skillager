from __future__ import annotations

import base64
import csv
import hashlib
import io
import tarfile
import zipfile
from email.message import Message
from pathlib import Path

NAME = "skillager"
VERSION = "0.1.1"
DIST = f"{NAME}-{VERSION}"
DIST_INFO = f"{NAME}-{VERSION}.dist-info"
ROOT = Path(__file__).resolve().parents[1]


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    wheel_name = f"{NAME}-{VERSION}-py3-none-any.whl"
    wheel_path = Path(wheel_directory) / wheel_name
    records: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted((ROOT / "src" / NAME).rglob("*.py")):
            arcname = f"{NAME}/{path.relative_to(ROOT / 'src' / NAME).as_posix()}"
            data = path.read_bytes()
            zf.writestr(arcname, data)
            records.append((arcname, data))
        metadata = _metadata().encode()
        wheel = "Wheel-Version: 1.0\nGenerator: skillager-build\nRoot-Is-Purelib: true\nTag: py3-none-any\n".encode()
        entry_points = "[console_scripts]\nskillager = skillager.cli:main\n".encode()
        license_data = (ROOT / "LICENSE").read_bytes()
        for arcname, data in (
            (f"{DIST_INFO}/METADATA", metadata),
            (f"{DIST_INFO}/WHEEL", wheel),
            (f"{DIST_INFO}/entry_points.txt", entry_points),
            (f"{DIST_INFO}/LICENSE", license_data),
        ):
            zf.writestr(arcname, data)
            records.append((arcname, data))
        record_path = f"{DIST_INFO}/RECORD"
        zf.writestr(record_path, _record(records, record_path))
    return wheel_name


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    wheel_name = f"{NAME}-{VERSION}-py3-none-any.whl"
    wheel_path = Path(wheel_directory) / wheel_name
    records: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        pth = str((ROOT / "src").resolve()) + "\n"
        license_data = (ROOT / "LICENSE").read_bytes()
        for arcname, data in (
            (f"{NAME}.pth", pth.encode()),
            (f"{DIST_INFO}/METADATA", _metadata().encode()),
            (f"{DIST_INFO}/WHEEL", "Wheel-Version: 1.0\nGenerator: skillager-build\nRoot-Is-Purelib: true\nTag: py3-none-any\n".encode()),
            (f"{DIST_INFO}/entry_points.txt", "[console_scripts]\nskillager = skillager.cli:main\n".encode()),
            (f"{DIST_INFO}/LICENSE", license_data),
        ):
            zf.writestr(arcname, data)
            records.append((arcname, data))
        record_path = f"{DIST_INFO}/RECORD"
        zf.writestr(record_path, _record(records, record_path))
    return wheel_name


def build_sdist(sdist_directory, config_settings=None):
    sdist_name = f"{DIST}.tar.gz"
    sdist_path = Path(sdist_directory) / sdist_name
    include_roots = ["build_backend", "docs", "examples", "src", "tests"]
    include_files = ["pyproject.toml", "README.md", "LICENSE"]
    with tarfile.open(sdist_path, "w:gz", format=tarfile.PAX_FORMAT) as tf:
        for filename in include_files:
            path = ROOT / filename
            if path.exists():
                tf.add(path, arcname=f"{DIST}/{filename}")
        for root_name in include_roots:
            root = ROOT / root_name
            if root.exists():
                for path in sorted(item for item in root.rglob("*") if item.is_file() and not _excluded(item)):
                    tf.add(path, arcname=f"{DIST}/{path.relative_to(ROOT).as_posix()}")
        pkg_info = _metadata().encode()
        info = tarfile.TarInfo(f"{DIST}/PKG-INFO")
        info.size = len(pkg_info)
        tf.addfile(info, io.BytesIO(pkg_info))
    return sdist_name


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    dist_info = Path(metadata_directory) / DIST_INFO
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "METADATA").write_text(_metadata(), encoding="utf-8")
    (dist_info / "WHEEL").write_text("Wheel-Version: 1.0\nGenerator: skillager-build\nRoot-Is-Purelib: true\nTag: py3-none-any\n", encoding="utf-8")
    (dist_info / "entry_points.txt").write_text("[console_scripts]\nskillager = skillager.cli:main\n", encoding="utf-8")
    return DIST_INFO


def get_requires_for_build_wheel(config_settings=None):
    return []


def get_requires_for_build_editable(config_settings=None):
    return []


def get_requires_for_build_sdist(config_settings=None):
    return []


def _metadata() -> str:
    message = Message()
    message["Metadata-Version"] = "2.1"
    message["Name"] = NAME
    message["Version"] = VERSION
    message["Summary"] = "A Python environment skill registry and activation layer for coding agents."
    message["Requires-Python"] = ">=3.11"
    message["Requires-Dist"] = "pyyaml>=6.0.3"
    message["Requires-Dist"] = "rich>=15.0.0"
    message["Provides-Extra"] = "test"
    message["License"] = "MIT"
    message["Keywords"] = "agents,skills,codex,claude,llm"
    for classifier in (
        "Development Status :: 3 - Alpha",
        "Environment :: Console",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Topic :: Software Development",
    ):
        message["Classifier"] = classifier
    message["Author"] = "Skillager Contributors"
    message["Description-Content-Type"] = "text/markdown"
    body = (ROOT / "README.md").read_text(encoding="utf-8")
    return message.as_string() + "\n" + body


def _excluded(path: Path) -> bool:
    if any(part in {".git", ".venv", ".skillager", ".codex", ".claude", ".agents"} for part in path.parts):
        return True
    if any(part.endswith(".egg-info") for part in path.parts):
        return True
    if any(part == "__pycache__" for part in path.parts):
        return True
    return path.suffix in {".pyc", ".pyo"}


def _record(records: list[tuple[str, bytes]], record_path: str) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    for arcname, data in records:
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
        writer.writerow([arcname, f"sha256={digest}", str(len(data))])
    writer.writerow([record_path, "", ""])
    return output.getvalue()
