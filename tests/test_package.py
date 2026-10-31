"""Exercise the shipped tool in isolated directories; no account or network access."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "manage.py"


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_tool(self, *args):
        return subprocess.run([sys.executable, str(TOOL), *map(str, args)],
                              text=True, capture_output=True)

    def test_init_creates_three_editable_notes_outside_skill(self):
        workspace = self.base / "workspace with spaces"
        workspace.mkdir()
        result = self.run_tool("init", "--workspace", workspace)
        self.assertEqual(result.returncode, 0, result.stderr)
        notes = workspace / "task-notes"
        self.assertEqual({p.name for p in notes.iterdir()},
                         {"PLAN.md", "QUESTIONS.md", "EVIDENCE.md"})
        self.assertIn("답변", (notes / "QUESTIONS.md").read_text())

    def test_init_never_overwrites_or_merges_existing_notes(self):
        workspace = self.base / "project"
        workspace.mkdir()
        notes = workspace / "task-notes"
        notes.mkdir()
        (notes / "QUESTIONS.md").write_text("human answer")
        result = self.run_tool("init", "--workspace", workspace)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((notes / "QUESTIONS.md").read_text(), "human answer")
        self.assertEqual(len(list(notes.iterdir())), 1)

    def test_init_refuses_missing_workspace(self):
        missing = self.base / "missing"
        result = self.run_tool("init", "--workspace", missing)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(missing.exists())

    def test_init_refuses_symlink_destination(self):
        workspace = self.base / "workspace"
        workspace.mkdir()
        outside = self.base / "outside"
        outside.mkdir()
        (workspace / "task-notes").symlink_to(outside, target_is_directory=True)
        result = self.run_tool("init", "--workspace", workspace)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(outside.iterdir()), [])

    def test_install_copies_self_contained_skill_without_replacing_it(self):
        destination = self.base / "cli skills"
        destination.mkdir()
        result = self.run_tool("install", "--destination", destination)
        self.assertEqual(result.returncode, 0, result.stderr)
        skill = destination / "assessment-workflow"
        self.assertTrue((skill / "SKILL.md").is_file())
        self.assertTrue((skill / "references" / "manual-questions.md").is_file())
        marker = skill / "human-note.txt"
        marker.write_text("preserve")
        again = self.run_tool("install", "--destination", destination)
        self.assertNotEqual(again.returncode, 0)
        self.assertEqual(marker.read_text(), "preserve")

    def test_package_is_allowlisted_reproducible_and_verifiable(self):
        first = self.base / "first.zip"
        second = self.base / "second.zip"
        for path in (first, second):
            result = self.run_tool("package", "--output", path)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        allowlist = json.loads((ROOT / "package-files.json").read_text())
        with zipfile.ZipFile(first) as archive:
            names = archive.namelist()
            prefix = names[0].split("/")[0] + "/"
            self.assertEqual(set(names), {prefix + p for p in allowlist} |
                             {prefix + "MANIFEST.sha256"})
            for line in archive.read(prefix + "MANIFEST.sha256").decode().splitlines():
                digest, relative = line.split("  ", 1)
                self.assertEqual(hashlib.sha256(archive.read(prefix + relative)).hexdigest(), digest)
        checksum = first.with_name(first.name + ".sha256").read_text().split()[0]
        self.assertEqual(checksum, hashlib.sha256(first.read_bytes()).hexdigest())

    def test_existing_archive_is_preserved(self):
        target = self.base / "existing.zip"
        target.write_bytes(b"keep")
        result = self.run_tool("package", "--output", target)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(target.read_bytes(), b"keep")

    def copied_distribution(self):
        copy = self.base / "distribution"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        return copy

    def run_copy(self, copy, *args):
        return subprocess.run([sys.executable, str(copy / "tools/manage.py"), *map(str, args)],
                              text=True, capture_output=True)

    def test_install_excludes_unlisted_files_inside_skill(self):
        copy = self.copied_distribution()
        (copy / "skills/assessment-workflow/private-task.txt").write_text("local-only")
        destination = self.base / "skills"
        destination.mkdir()
        result = self.run_copy(copy, "install", "--destination", destination)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((destination / "assessment-workflow/private-task.txt").exists())

    def test_package_excludes_unlisted_notes_and_environment(self):
        copy = self.copied_distribution()
        (copy / "task-notes").mkdir()
        (copy / "task-notes/QUESTIONS.md").write_text("local-only")
        (copy / ".env").write_text("LOCAL_FIXTURE=example")
        target = self.base / "clean.zip"
        result = self.run_copy(copy, "package", "--output", target)
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(target) as archive:
            self.assertFalse(any("task-notes/" in n or n.endswith("/.env") for n in archive.namelist()))

    def test_package_rejects_symlinked_allowlisted_input(self):
        copy = self.copied_distribution()
        outside = self.base / "outside.txt"
        outside.write_text("local-only")
        (copy / "README.md").unlink()
        (copy / "README.md").symlink_to(outside)
        target = self.base / "refused.zip"
        result = self.run_copy(copy, "package", "--output", target)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(target.exists())

    def test_package_rejects_parent_traversal_in_allowlist(self):
        copy = self.copied_distribution()
        (self.base / "outside.txt").write_text("local-only")
        (copy / "package-files.json").write_text('["../outside.txt"]')
        target = self.base / "refused.zip"
        result = self.run_copy(copy, "package", "--output", target)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(target.exists())

    def test_init_refuses_to_mix_task_notes_into_distribution(self):
        copy = self.copied_distribution()
        result = self.run_copy(copy, "init", "--workspace", copy)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((copy / "task-notes").exists())

    def test_existing_checksum_prevents_archive_creation(self):
        target = self.base / "bundle.zip"
        checksum = self.base / "bundle.zip.sha256"
        checksum.write_text("preserve")
        result = self.run_tool("package", "--output", target)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(target.exists())
        self.assertEqual(checksum.read_text(), "preserve")

    def test_extracted_package_can_initialize_notes(self):
        target = self.base / "bundle.zip"
        result = self.run_tool("package", "--output", target)
        self.assertEqual(result.returncode, 0, result.stderr)
        extracted = self.base / "extracted"
        with zipfile.ZipFile(target) as archive:
            archive.extractall(extracted)
        unpacked = next(extracted.iterdir())
        workspace = self.base / "fresh task"
        workspace.mkdir()
        result = subprocess.run([sys.executable, str(unpacked / "tools/manage.py"),
                                 "init", "--workspace", str(workspace)],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((workspace / "task-notes/QUESTIONS.md").is_file())


if __name__ == "__main__":
    unittest.main()
