#!/usr/bin/env python3
"""Offline, standard-library-only copying and packaging helpers."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILL_NAME = "workflow"
SKILL = ROOT / "skills" / SKILL_NAME
NOTES = ("PLAN.md", "QUESTIONS.md", "EVIDENCE.md")


def inside(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def existing_directory(value):
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError("Directory required: " + str(path))
    return path


def fresh_destination(parent, name):
    target = parent / name
    if target.exists() or target.is_symlink():
        raise ValueError("Destination already exists; preserved: " + str(target))
    return target


def initialize(workspace):
    workspace = existing_directory(workspace)
    if inside(workspace, ROOT):
        raise ValueError("Choose a task workspace outside the distribution directory.")
    target = fresh_destination(workspace, "task-notes")
    sources = [SKILL / "assets" / name for name in NOTES]
    payloads = [(source.name, source.read_bytes()) for source in sources]
    target.mkdir()
    for name, content in payloads:
        with (target / name).open("xb") as handle:
            handle.write(content)
    print("Created editable notes: " + str(target))
    print("The user manually sends questions and records replies in QUESTIONS.md.")


def install(destination=None, workspace=None):
    if workspace is not None:
        project = existing_directory(workspace)
        if inside(project, ROOT):
            raise ValueError("Choose a task workspace outside this distribution.")
        parent = project / ".agents" / "skills"
        for path in (project / ".agents", parent):
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise ValueError("Project skill directory must be a real directory: " + str(path))
    else:
        parent = existing_directory(destination)
        if inside(parent, ROOT):
            raise ValueError("Choose an installation directory outside this distribution.")
    target = fresh_destination(parent, SKILL_NAME)
    _, payloads = package_payloads()
    prefix = "skills/" + SKILL_NAME + "/"
    selected = {name[len(prefix):]: data for name, data in payloads.items() if name.startswith(prefix)}
    if "SKILL.md" not in selected:
        raise ValueError("The allowlist does not contain the skill entrypoint.")
    parent.mkdir(parents=True, exist_ok=True)
    target.mkdir()
    for relative, data in selected.items():
        output = target / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as handle:
            handle.write(data)
    print("Copied skill to: " + str(target))
    print("Native discovery depends on the CLI. Manual SKILL.md reading is also supported.")
    print("Manual entrypoint: " + str(target / "SKILL.md"))


def package_payloads():
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("VERSION must be a numeric semantic version.")
    entries = json.loads((ROOT / "package-files.json").read_text(encoding="utf-8"))
    if not isinstance(entries, list) or any(not isinstance(p, str) for p in entries):
        raise ValueError("The package allowlist must be a list of relative paths.")
    if len(entries) != len(set(entries)):
        raise ValueError("Duplicate allowlist entry.")
    payloads = {}
    for entry in sorted(entries):
        relative = PurePosixPath(entry)
        if relative.is_absolute() or ".." in relative.parts or "\\" in entry or str(relative) != entry:
            raise ValueError("Unsafe allowlist entry: " + entry)
        source = ROOT / entry
        cursor = source
        while cursor != ROOT:
            if cursor.is_symlink():
                raise ValueError("Symlink in package input: " + entry)
            cursor = cursor.parent
        if not source.is_file() or not inside(source.resolve(), ROOT):
            raise ValueError("Missing or invalid package input: " + entry)
        payloads[entry] = source.read_bytes()
    return version, payloads


def build_package(output):
    output = Path(output).expanduser()
    parent = existing_directory(output.parent)
    target = fresh_destination(parent, output.name)
    checksum = fresh_destination(parent, output.name + ".sha256")
    version, payloads = package_payloads()
    manifest = "".join(hashlib.sha256(data).hexdigest() + "  " + name + "\n"
                       for name, data in sorted(payloads.items()))
    payloads["MANIFEST.sha256"] = manifest.encode("utf-8")
    prefix = "cofa-" + version + "/"
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(payloads.items()):
            info = zipfile.ZipInfo(prefix + name, date_time=(2020, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=9)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    with checksum.open("x", encoding="utf-8") as handle:
        handle.write(digest + "  " + target.name + "\n")
    print("Created: " + str(target))
    print("SHA256: " + digest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Copy blank notes into an existing task workspace")
    init.add_argument("--workspace", required=True)
    install_parser = commands.add_parser("install", help="Copy skill into a CLI directory or task workspace")
    location = install_parser.add_mutually_exclusive_group(required=True)
    location.add_argument("--destination", help="Existing CLI skills directory")
    location.add_argument("--workspace", help="Existing task folder; installs under .agents/skills")
    pack = commands.add_parser("package", help="Build an allowlisted offline ZIP and checksum")
    pack.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.command == "init":
            initialize(args.workspace)
        elif args.command == "install":
            install(destination=args.destination, workspace=args.workspace)
        else:
            build_package(args.output)
    except (OSError, ValueError) as error:
        print("Error: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
