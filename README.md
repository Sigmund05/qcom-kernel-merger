# qcom-kernel-merger

Takes an OEM kernel source for a Qualcomm device and puts it on top of the
**closest tag** in Qualcomm's own kernel sources on
[CodeLinaro (CLO)](https://git.codelinaro.org/clo/la/kernel).

OEM kernel sources usually ship as a plain archive with no git history. This
tool works out which CLO tag the source branched from, builds a repository with
that tag as the first commit, and records the OEM's changes as a single commit
on top of it.

```
$ qcmerge ~/src/oem-kernel -o ~/work/merged

Closest tag candidates:
   1. LA.UM.9.14.r1-19700-LAHAINA.0  score 0.9312  same 68142  modified 3120  oem only 1804  tag only 212
   2. LA.UM.9.14.r1-19600-LAHAINA.0  score 0.9188  same 67235  modified 4011  oem only 1804  tag only 240
   ...

Done.
  result repository : /home/me/work/merged
  base tag          : LA.UM.9.14.r1-19700-LAHAINA.0 (eba5b98c68f5)
  branch            : vendor (bf402985995e)
  OEM changes       : 3120 modified, 1804 added, 212 removed
```

In the result repository, `git diff <base tag>..vendor` is exactly the OEM's
changes.

## How it works

1. **Detect the kernel version** — read `VERSION`, `PATCHLEVEL` and `SUBLEVEL`
   from the OEM source's top-level `Makefile`.
2. **Pick the CLO repository** — up to and including 5.15, the per-series
   repositories (`msm-3.18`, `msm-4.9`, `msm-5.4` and so on); past it, the
   merged `qcom` repository. Long-term support goes straight from 5.15 to
   6.1, and an Android device kernel only ever tracks an LTS release, so a
   series outside that set is flagged as suspicious.
3. **Find the closest tag**
   - List the tags with `git ls-remote --tags`.
   - Fetch **commits and trees only** with a
     `--filter=blob:none --depth=1` partial clone. The blob hashes recorded in
     those trees are enough to tell whether two files hold the same contents,
     so gigabytes of blobs never have to be downloaded.
   - **First pass**: compare top-level tree entries only. A directory's tree
     hash matches only when everything below it is identical, which makes this
     a very cheap measure of how much the OEM left untouched. Tags tied at the
     cut-off are all kept.
   - **Second pass**: for each surviving candidate, expand the full file list
     with `git ls-tree -r`, compare it against the OEM tree and score it with
     the Jaccard index (`identical files / total distinct paths`).
4. **Merge** — fetch the winning tag with its blobs, put it in as the first
   commit and lay the whole OEM source tree on top of it as a single commit.

```
result repository
  * bf40298 (vendor)  vendor: import oem-kernel kernel source   <- OEM changes
  * eba5b98 (tag: LA.UM.9.14.r1-19700-LAHAINA.0)                <- CLO original
```

## Installing

Python 3.9 or newer and git. No third-party dependencies.

```sh
pip install -e .
# or, without installing
python3 -m qcmerge --help
```

## Usage

```sh
qcmerge [options] <oem kernel source path>
```

| Option | Description |
| --- | --- |
| `-o, --output DIR` | Result repository path (default: `qcmerge-out`). Must be empty |
| `-b, --branch NAME` | Branch to put the OEM source on (default: `vendor`) |
| `--cache-dir DIR` | Tag cache repository (default: `~/.cache/qcom-kernel-merger/<repo>`) |
| `--clo-base URL` | CLO kernel group URL |
| `--repo NAME` | Repository name to use instead of the detected one (`msm-5.4`, `qcom` …) |
| `--tag-pattern GLOB` | Glob pattern narrowing the candidate tags; may be given more than once |
| `-j, --jobs N` | Comparisons to run at once (default: CPU count) |
| `--prefilter-keep N` | Tags kept by the first pass; `0` compares everything |
| `--batch-size N` | Tags requested per fetch |
| `--top N` | How many tag candidates to print |
| `--depth N` | Depth to fetch the base tag at; `0` for full history |
| `-m, --message TEXT` | Message for the OEM source commit |
| `--no-checkout` | Do not check the source out into the result work tree |

When the chipset or release line is already known, narrowing the candidates
with `--tag-pattern` is much faster.

```sh
qcmerge ~/src/oem-kernel --tag-pattern 'LA.UM.9.14*LAHAINA*'
```

## Things worth knowing

- **Disk**: the tag cache holds no blobs, but it does hold tree objects. A
  repository with thousands of tags can take hundreds of megabytes. The cache
  is reused as-is on the next run.
- **`.gitignore`**: the OEM source's own `.gitignore` files apply. A kernel
  tree's `.gitignore` only excludes build output, so the comparison against
  CLO tags stays on the same footing.
- **The source directory**: the OEM source is only ever read, through
  `--work-tree`. A `.git` inside it would be recorded as a submodule, so that
  case stops with an error.
- **What is compared**: only whether file contents are identical (the blob
  hash). File mode changes and line-level similarity do not affect the score.

## Tests

A fake CLO repository is built locally, so the whole flow is covered without
network access.

```sh
python3 -m unittest discover -s tests -t .
```

## License

Not chosen yet.
