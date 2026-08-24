"""Tests for kernel version detection."""

import os
import tempfile
import unittest

from qcmerge import kernel
from qcmerge.errors import QcMergeError

from .util import kernel_makefile


class ParseMakefileTest(unittest.TestCase):
    def test_reads_version_fields(self):
        version = kernel.parse_makefile(kernel_makefile(5, 4, 210))
        self.assertEqual(version.version, 5)
        self.assertEqual(version.patchlevel, 4)
        self.assertEqual(version.sublevel, 210)
        self.assertEqual(version.series, "5.4")
        self.assertEqual(version.release, "5.4.210")

    def test_keeps_extraversion(self):
        version = kernel.parse_makefile(
            "VERSION = 6\nPATCHLEVEL = 1\nSUBLEVEL = 57\nEXTRAVERSION = -rc2\n"
        )
        self.assertEqual(version.full, "6.1.57-rc2")

    def test_ignores_later_assignments(self):
        text = "VERSION = 4\nPATCHLEVEL = 19\nSUBLEVEL = 157\n" + "VERSION = 9\n" * 5
        self.assertEqual(kernel.parse_makefile(text).version, 4)

    def test_returns_none_for_non_kernel_makefile(self):
        self.assertIsNone(kernel.parse_makefile("all:\n\techo hi\n"))

    def test_qcom_repo_boundary(self):
        self.assertFalse(kernel.KernelVersion(5, 15, 78).uses_qcom_repo())
        self.assertFalse(kernel.KernelVersion(6, 0, 12).uses_qcom_repo())
        self.assertTrue(kernel.KernelVersion(6, 1, 0).uses_qcom_repo())
        self.assertTrue(kernel.KernelVersion(6, 6, 30).uses_qcom_repo())


class DetectTest(unittest.TestCase):
    def test_detects_from_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "Makefile"), "w", encoding="utf-8") as handle:
                handle.write(kernel_makefile(4, 9, 300))
            self.assertEqual(kernel.detect(tmp).release, "4.9.300")

    def test_error_mentions_nested_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = os.path.join(tmp, "kernel")
            os.makedirs(nested)
            with open(os.path.join(nested, "Makefile"), "w", encoding="utf-8") as handle:
                handle.write(kernel_makefile(4, 14, 190))
            with self.assertRaises(QcMergeError) as ctx:
                kernel.detect(tmp)
            self.assertIn(nested, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
