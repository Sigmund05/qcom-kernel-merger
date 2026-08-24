"""Tests for CLO repository addressing and tag listing."""

import unittest

from qcmerge import clo
from qcmerge.kernel import KernelVersion


class RepoNameTest(unittest.TestCase):
    def test_msm_repo_below_6_1(self):
        self.assertEqual(clo.repo_name(KernelVersion(3, 18, 140)), "msm-3.18")
        self.assertEqual(clo.repo_name(KernelVersion(4, 4, 302)), "msm-4.4")
        self.assertEqual(clo.repo_name(KernelVersion(5, 10, 200)), "msm-5.10")
        self.assertEqual(clo.repo_name(KernelVersion(6, 0, 5)), "msm-6.0")

    def test_qcom_repo_from_6_1(self):
        self.assertEqual(clo.repo_name(KernelVersion(6, 1, 57)), "qcom")
        self.assertEqual(clo.repo_name(KernelVersion(6, 12, 3)), "qcom")

    def test_url(self):
        self.assertEqual(
            clo.repo_url(KernelVersion(5, 4, 210)),
            "https://git.codelinaro.org/clo/la/kernel/msm-5.4.git",
        )
        self.assertEqual(
            clo.repo_url(KernelVersion(6, 1, 0), base="https://example.org/k/"),
            "https://example.org/k/qcom.git",
        )


class LsRemoteTest(unittest.TestCase):
    def test_dedups_peeled_tags(self):
        output = (
            "aaa\trefs/heads/main\n"
            "bbb\trefs/tags/LA.UM.9.14.r1-19700-LAHAINA.0\n"
            "ccc\trefs/tags/LA.UM.9.14.r1-19700-LAHAINA.0^{}\n"
            "ddd\trefs/tags/LE.UM.5.4.r1-01000-QCM6490.0\n"
        )
        self.assertEqual(
            clo.parse_ls_remote(output),
            ["LA.UM.9.14.r1-19700-LAHAINA.0", "LE.UM.5.4.r1-01000-QCM6490.0"],
        )

    def test_ignores_non_tag_refs(self):
        self.assertEqual(clo.parse_ls_remote("aaa\trefs/heads/main\n"), [])


class FilterTagsTest(unittest.TestCase):
    tags = ["LA.UM.9.14-LAHAINA", "LA.UM.9.12-KONA", "LE.UM.5.4-QCM6490"]

    def test_no_pattern_keeps_everything(self):
        self.assertEqual(clo.filter_tags(self.tags), self.tags)

    def test_glob_pattern(self):
        self.assertEqual(clo.filter_tags(self.tags, ["*LAHAINA*"]), ["LA.UM.9.14-LAHAINA"])

    def test_multiple_patterns_are_or(self):
        self.assertEqual(
            clo.filter_tags(self.tags, ["*KONA*", "LE.UM.*"]),
            ["LA.UM.9.12-KONA", "LE.UM.5.4-QCM6490"],
        )


if __name__ == "__main__":
    unittest.main()
