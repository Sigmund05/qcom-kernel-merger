"""Exercise the whole flow against a fake CLO repository built locally.

Nothing reaches the real CodeLinaro, so these tests run without network
access.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from qcmerge import cli, tagsearch, vendor
from qcmerge.gitcmd import Git

from .util import build_clo_fixture, clo_base_url, git, kernel_makefile, write_tree

TAG_A = "LA.UM.9.14.r1-01000-LAHAINA.0"
TAG_B = "LA.UM.9.14.r1-02000-LAHAINA.0"
TAG_C = "LA.UM.9.14.r1-03000-LAHAINA.0"
TAG_D = "LA.UM.9.14.r1-04000-LAHAINA.0"


def snapshot(sublevel: int, smem: str, main: str, sched: str) -> dict:
    return {
        "Makefile": kernel_makefile(5, 4, sublevel),
        "Documentation/qcom.txt": "qualcomm platform notes\n",
        "arch/arm64/Kconfig": "config ARM64\n\tbool\n",
        "drivers/soc/qcom/smem.c": smem,
        "init/main.c": main,
        "kernel/sched/core.c": sched,
    }


SNAPSHOTS = [
    (TAG_A, snapshot(100, "smem v1\n", "main v1\n", "sched v1\n")),
    (TAG_B, snapshot(120, "smem v2\n", "main v1\n", "sched v1\n")),
    (TAG_C, snapshot(140, "smem v2\n", "main v2\n", "sched v1\n")),
    (TAG_D, snapshot(160, "smem v2\n", "main v2\n", "sched v2\n")),
]


def vendor_source(root: str) -> None:
    """Derive an OEM source from TAG_C with a few vendor changes."""
    files = dict(SNAPSHOTS[2][1])
    files["drivers/soc/qcom/smem.c"] = "smem v2\n/* oem tweak */\n"
    files["drivers/oem/oem_driver.c"] = "int oem_probe(void) { return 0; }\n"
    del files["Documentation/qcom.txt"]
    write_tree(root, files)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.group = build_clo_fixture(self.tmp, "msm-5.4", SNAPSHOTS)
        self.source = os.path.join(self.tmp, "oem-kernel")
        os.makedirs(self.source)
        vendor_source(self.source)
        self.out = os.path.join(self.tmp, "out")
        self.cache = os.path.join(self.tmp, "cache")

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *extra: str) -> str:
        argv = [
            self.source,
            "-o", self.out,
            "--cache-dir", self.cache,
            "--clo-base", clo_base_url(self.group),
            "-q",
        ] + list(extra)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(argv)
        self.assertEqual(code, 0, buffer.getvalue())
        return buffer.getvalue()

    # ----------------------------------------------------------------- checks
    def test_picks_closest_tag_and_builds_repo(self):
        output = self.run_cli()
        self.assertIn(TAG_C, output)

        # The base tag made it into the result repository.
        self.assertEqual(
            git("rev-parse", TAG_C + "^{commit}", cwd=self.out),
            git("rev-parse", "vendor^", cwd=self.out),
        )

        # The OEM changes come out as a single commit.
        diff = git("diff", "--name-status", TAG_C + "..vendor", cwd=self.out).splitlines()
        self.assertEqual(
            sorted(diff),
            sorted(
                [
                    "D\tDocumentation/qcom.txt",
                    "A\tdrivers/oem/oem_driver.c",
                    "M\tdrivers/soc/qcom/smem.c",
                ]
            ),
        )

        # The source is checked out into the work tree.
        self.assertTrue(os.path.isfile(os.path.join(self.out, "drivers/oem/oem_driver.c")))
        self.assertFalse(os.path.exists(os.path.join(self.out, "Documentation/qcom.txt")))

    def test_commit_message_records_base(self):
        self.run_cli()
        message = git("log", "-1", "--format=%B", "vendor", cwd=self.out)
        self.assertIn("Base tag: " + TAG_C, message)
        self.assertIn("Kernel version: 5.4.140", message)

    def test_custom_message_and_branch(self):
        self.run_cli("-m", "oem: import", "-b", "oem")
        self.assertEqual(git("log", "-1", "--format=%s", "oem", cwd=self.out), "oem: import")

    def test_no_checkout_leaves_worktree_empty(self):
        self.run_cli("--no-checkout")
        self.assertFalse(os.path.exists(os.path.join(self.out, "init/main.c")))
        self.assertTrue(git("rev-parse", "vendor", cwd=self.out))

    def test_tag_pattern_narrows_candidates(self):
        output = self.run_cli("--tag-pattern", "*-02000-*")
        self.assertIn(TAG_B, output)
        self.assertNotIn(TAG_C, output)

    def test_cache_is_reused(self):
        self.run_cli()
        searcher = tagsearch.TagSearcher(self.cache, clo_base_url(self.group))
        self.assertEqual(
            searcher.local_tags(), {TAG_A, TAG_B, TAG_C, TAG_D}
        )

    def test_refuses_non_empty_output(self):
        os.makedirs(self.out)
        with open(os.path.join(self.out, "keep.txt"), "w", encoding="utf-8") as handle:
            handle.write("x\n")
        self.assertEqual(cli.main([self.source, "-o", self.out, "-q"]), 2)


class PrefilterTest(unittest.TestCase):
    """The first pass that narrows candidates by top-level tree entries."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.group = build_clo_fixture(self.tmp, "msm-5.4", SNAPSHOTS)
        self.source = os.path.join(self.tmp, "oem-kernel")
        os.makedirs(self.source)
        vendor_source(self.source)

    def tearDown(self):
        self._tmp.cleanup()

    def test_keeps_the_closest_tag(self):
        work = os.path.join(self.tmp, "work")
        os.makedirs(work)
        Git(cwd=work).run("init", "--quiet", "-b", "vendor", work)
        repo = Git(git_dir=os.path.join(work, ".git"), work_tree=work, cwd=work)
        tree = vendor.index_source(repo, self.source, os.path.join(work, ".git", "index"))

        searcher = tagsearch.TagSearcher(
            os.path.join(self.tmp, "cache"), clo_base_url(self.group) + "/msm-5.4.git"
        )
        searcher.prepare()
        searcher.fetch_tags([tag for tag, _ in SNAPSHOTS])
        kept = searcher.prefilter([tag for tag, _ in SNAPSHOTS], tree.top_level, keep=1)
        self.assertIn(TAG_C, kept)

        scores = searcher.score_tags(kept, tree.files)
        self.assertEqual(scores[0].tag, TAG_C)
        self.assertEqual(scores[0].modified, 1)
        self.assertEqual(scores[0].only_vendor, 1)
        self.assertEqual(scores[0].only_tag, 1)


if __name__ == "__main__":
    unittest.main()
