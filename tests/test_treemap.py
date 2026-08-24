"""트리 파싱과 유사도 계산 테스트."""

import os
import subprocess
import tempfile
import unittest

from qcmerge import treemap

from .util import git, write_tree


class ParseLsTreeTest(unittest.TestCase):
    def test_parses_paths_and_hashes(self):
        data = (
            b"100644 blob " + b"a" * 40 + b"\tMakefile\x00"
            b"120000 blob " + b"b" * 40 + b"\tlink\x00"
            b"160000 commit " + b"c" * 40 + b"\tsub\x00"
        )
        parsed = treemap.parse_ls_tree_z(data)
        self.assertEqual(
            parsed,
            {b"Makefile": b"a" * 40, b"link": b"b" * 40, b"sub": b"c" * 40},
        )

    def test_empty_input(self):
        self.assertEqual(treemap.parse_ls_tree_z(b""), {})


class RealGitTreeTest(unittest.TestCase):
    """git 이 실제로 만든 트리를 대상으로 파싱을 검증한다."""

    def test_tree_object_matches_ls_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            os.makedirs(repo)
            git("init", "--quiet", "-b", "main", ".", cwd=repo)
            write_tree(
                repo,
                {
                    "Makefile": "VERSION = 5\n",
                    "arch/arm64/Kconfig": "config ARM64\n",
                    "drivers/soc/qcom/foo.c": "int foo;\n",
                },
            )
            git("add", "-A", ".", cwd=repo)
            git("commit", "--quiet", "-m", "init", cwd=repo)

            expected = {}
            for line in git("ls-tree", "HEAD", cwd=repo).splitlines():
                head, _, name = line.partition("\t")
                expected[name.encode()] = head.split()[2].encode()

            raw = subprocess.run(
                ["git", "cat-file", "tree", "HEAD^{tree}"],
                cwd=repo,
                stdout=subprocess.PIPE,
                check=True,
            ).stdout
            self.assertEqual(treemap.parse_tree_object(raw), expected)


class BatchStreamTest(unittest.TestCase):
    def test_reads_objects_and_missing(self):
        data = b"deadbeef tree 4\nAAAA\nsomething missing\n"
        items = list(treemap.iter_batch_objects(data))
        self.assertEqual(items, [("tree", b"AAAA"), (None, b"")])


class ScoreTest(unittest.TestCase):
    vendor = {b"a": b"1", b"b": b"2", b"c": b"3"}

    def test_identical_trees(self):
        score = treemap.score_maps("t", self.vendor, dict(self.vendor))
        self.assertEqual(score.matched, 3)
        self.assertEqual(score.changed, 0)
        self.assertEqual(score.score, 1.0)

    def test_counts_each_difference_kind(self):
        candidate = {b"a": b"1", b"b": b"changed", b"d": b"4"}
        score = treemap.score_maps("t", self.vendor, candidate)
        self.assertEqual(score.matched, 1)     # a
        self.assertEqual(score.modified, 1)    # b
        self.assertEqual(score.only_vendor, 1) # c
        self.assertEqual(score.only_tag, 1)    # d
        self.assertEqual(score.union, 4)
        self.assertAlmostEqual(score.score, 0.25)
        self.assertEqual(score.vendor_total, 3)
        self.assertEqual(score.tag_total, 3)

    def test_disjoint_trees(self):
        score = treemap.score_maps("t", self.vendor, {b"z": b"9"})
        self.assertEqual(score.score, 0.0)

    def test_rank_prefers_higher_score(self):
        good = treemap.score_maps("good", self.vendor, dict(self.vendor))
        poor = treemap.score_maps("poor", self.vendor, {b"a": b"1"})
        self.assertEqual([s.tag for s in treemap.rank([poor, good])], ["good", "poor"])


if __name__ == "__main__":
    unittest.main()
