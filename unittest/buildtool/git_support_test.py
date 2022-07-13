# Copyright 2017 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# pylint: disable=missing-docstring

import argparse
import datetime
import os
import shutil
import tempfile
import unittest
import yaml

import dateutil.parser

from buildtool import (
    CommitMessage,
    GitRepositorySpec,
    GitRunner,
    RepositorySummary,
    SemanticVersion,
    check_subprocess,
    check_subprocess_sequence,
)

from test_util import init_runtime


TAG_VERSION_PATTERN = r"^v[0-9]+\.[0-9]+\.[0-9]+$"

VERSION_BASE = "v0.1.0"
VERSION_A = "v0.4.0"
VERSION_B = "v0.5.0"
BRANCH_A = "release-a"
BRANCH_B = "release-b"
BRANCH_C = "release-c"
BRANCH_BASE = "baseline"
UPSTREAM_USER = "unittest"
TEST_REPO_NAME = "test_repository"


def make_default_options():
    """Helper function for creating default options for runner."""
    parser = argparse.ArgumentParser()
    GitRunner.add_parser_args(parser, {"github_disable_upstream_push": True})
    parser.add_argument(
        "--output_dir", default=os.path.join("/tmp", "gittest.%d" % os.getpid())
    )
    return parser.parse_args([])


class TestGitRunner(unittest.TestCase):
    @classmethod
    def run_git(cls, command):
        return check_subprocess(
            f'git -C "{cls.git_dir}" {command}'
        )

    @classmethod
    def setUpClass(cls):
        cls.git = GitRunner(make_default_options())
        cls.base_temp_dir = tempfile.mkdtemp(prefix="git_test")
        cls.git_dir = os.path.join(cls.base_temp_dir, UPSTREAM_USER, TEST_REPO_NAME)
        os.makedirs(cls.git_dir)

        git_dir = cls.git_dir
        gitify = lambda args: f'git -C "{git_dir}" {args}'
        check_subprocess_sequence(
            [
                gitify("init"),
                f'touch "{git_dir}/base_file"',
                gitify(f'add "{git_dir}/base_file"'),
                gitify('commit -a -m "feat(test): added file"'),
                gitify(f"tag {VERSION_BASE} HEAD"),
                gitify(f"checkout -b {BRANCH_BASE}"),
                gitify(f"checkout -b {BRANCH_A}"),
                f'touch "{git_dir}/a_file"',
                gitify(f'add "{git_dir}/a_file"'),
                gitify('commit -a -m "feat(test): added a_file"'),
                gitify(f"tag {VERSION_A} HEAD"),
                gitify("checkout master"),
                gitify(f"checkout -b {BRANCH_B}"),
                f'touch "{git_dir}/b_file"',
                gitify(f'add "{git_dir}/b_file"'),
                gitify('commit -a -m "feat(test): added b_file"'),
                gitify(f"tag {VERSION_B} HEAD"),
                gitify("checkout master"),
                f'touch "{git_dir}/master_file"',
                gitify(f'add "{git_dir}/master_file"'),
                gitify('commit -a -m "feat(test): added master_file"'),
                gitify(f"checkout -b {BRANCH_C}"),
                f'touch "{git_dir}/c_file"',
                gitify(f'add "{git_dir}/c_file"'),
                gitify('commit -a -m "feat(test): added c_file"'),
                gitify("checkout master"),
                f'touch "{git_dir}/extra_file"',
                gitify(f'add "{git_dir}/extra_file"'),
                gitify('commit -a -m "feat(test): added extra_file"'),
                gitify("tag v9.9.9 HEAD"),
            ]
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base_temp_dir)

    def setUp(self):
        self.run_git(f"checkout master")

    def test_query_local_repository_branch(self):
        initial_branch = self.git.query_local_repository_branch(self.git_dir)
        self.assertEqual("master", initial_branch)

        self.run_git("checkout -b branch_test")
        final_branch = self.git.query_local_repository_branch(self.git_dir)
        self.assertEqual("branch_test", final_branch)

    def test_find_newest_tag_and_common_commit_from_id(self):
        # pylint: disable=too-many-locals
        git = self.git

        tests = [
            (BRANCH_BASE, VERSION_BASE),
            (BRANCH_A, VERSION_A),
            (BRANCH_B, VERSION_B),
            (BRANCH_B, VERSION_B),
        ]
        for branch, version in tests:
            self.run_git("checkout " + branch)
            head_commit_id = git.query_local_repository_commit_id(self.git_dir)
            tag, _ = git.find_newest_tag_and_common_commit_from_id(
                self.git_dir, head_commit_id
            )

            self.assertEqual(version, tag)

    def test_is_same_repo(self):
        variants = [
            "http://github.com/user/spinnaker",
            "http://github.com/user/spinnaker.git",
            "https://github.com/user/spinnaker",
            "https://github.com/user/spinnaker.git",
            "git@github.com:user/spinnaker.git",
            "git@github.com:user/spinnaker.git",
        ]
        for url in variants:
            self.assertTrue(GitRunner.is_same_repo(variants[0], url))

    def test_different_repo(self):
        variants = [
            "http://github.com/user/spinnaker",
            "http://github.com/path/user/spinnaker",
            "http://github.com/user/spinnaker/path",
            "http://github.com/user/spinnaker.github",
            "http://github/user/spinnaker",
            "http://mydomain.com/user/spinnaker",
            "path/user/spinnaker",
        ]
        for url in variants[1:]:
            self.assertFalse(GitRunner.is_same_repo(variants[0], url))

    def test_query_local_repository_commits_between_two_commit_ids(self):
        git = self.git
        test_method = git.query_local_repository_commits_between_two_commit_ids

        tests = [(BRANCH_A, VERSION_A), (BRANCH_B, VERSION_B)]

        for branch, version in tests:
            new_version = str(version)
            new_version = new_version[:-1] + "1"
            self.run_git("checkout " + branch)
            self.run_git(f"checkout -b {branch}-patch")
            start_commit_id = git.query_local_repository_commit_id(self.git_dir)
            new_messages = []
            for change in ["first", "second"]:
                new_path = os.path.join(self.git_dir, change + "_file")
                check_subprocess(f'touch "{new_path}"')
                self.run_git(f'add "{new_path}"')
                message = "fix(test): Made {change} change for testing.".format(
                    change=change
                )
                self.run_git(f'commit -a -m "{message}"')
                new_messages.append(" " * 4 + message)

            # Clone the repo because the <test_method> only works on remote repositories
            # so we need to give a local repository in front of the test repo we set up.
            # The reason for the remote constraint is because it wants to use "branch -r".
            clone_dir = os.path.join(self.base_temp_dir, "tag_at_patch", branch)
            os.makedirs(clone_dir)
            check_subprocess(
                "git clone {source} {target}".format(
                    source=self.git_dir, target=clone_dir
                )
            )
            head_commit_id = git.query_local_repository_commit_id(clone_dir)

            # The new messages show as we are providing pre and post commit ids in
            # the correct chronological order
            base_to_head_messages = test_method(
                clone_dir, start_commit_id, head_commit_id
            )
            self.assertEqual(2, len(base_to_head_messages))
            self.assertEqual(
                sorted(new_messages, reverse=True),
                [m.message for m in base_to_head_messages],
            )

            # The new messages won't show if we provide pre and post commit ids in
            # the incorrect chronological order
            head_to_base_messages = test_method(
                clone_dir, head_commit_id, start_commit_id
            )
            self.assertEqual(0, len(head_to_base_messages))

    def test_clone_upstream(self):
        git = self.git
        test_parent = os.path.join(self.base_temp_dir, "test_clone_upstream")
        os.makedirs(test_parent)

        test_dir = os.path.join(test_parent, TEST_REPO_NAME)
        repository = GitRepositorySpec(
            TEST_REPO_NAME, git_dir=test_dir, origin=self.git_dir
        )
        git.clone_repository_to_path(repository)
        self.assertTrue(os.path.exists(os.path.join(test_dir, "base_file")))

        want_tags = git.query_tag_commits(self.git_dir, TAG_VERSION_PATTERN)
        have_tags = git.query_tag_commits(test_dir, TAG_VERSION_PATTERN)
        self.assertEqual(want_tags, have_tags)

        got = check_subprocess(f'git -C "{test_dir}" remote -v')
        # Disable pushes to the origni
        # No upstream since origin is upstream
        self.assertEqual(
            "\n".join(
                [
                    f"origin\t{self.git_dir} (fetch)",
                    "origin\tdisabled (push)",
                ]
            ),
            got,
        )

        reference = git.determine_git_repository_spec(test_dir)
        expect = GitRepositorySpec(
            os.path.basename(self.git_dir), origin=self.git_dir, git_dir=test_dir
        )
        self.assertEqual(expect, reference)

    def test_clone_origin(self):
        git = self.git

        # Make the origin we're going to test the clone against
        # This is intentionally different from upstream so that
        # we can confirm that upstream is also setup properly.
        origin_user = "origin_user"
        origin_basedir = os.path.join(self.base_temp_dir, origin_user)
        os.makedirs(origin_basedir)
        check_subprocess(
            'git -C "{origin_dir}" clone "{upstream}"'.format(
                origin_dir=origin_basedir, upstream=self.git_dir
            )
        )

        test_parent = os.path.join(self.base_temp_dir, "test_clone_origin")
        os.makedirs(test_parent)

        test_dir = os.path.join(test_parent, TEST_REPO_NAME)
        origin_dir = os.path.join(origin_basedir, TEST_REPO_NAME)
        repository = GitRepositorySpec(
            TEST_REPO_NAME, git_dir=test_dir, origin=origin_dir, upstream=self.git_dir
        )
        self.git.clone_repository_to_path(repository)

        want_tags = git.query_tag_commits(self.git_dir, TAG_VERSION_PATTERN)
        have_tags = git.query_tag_commits(test_dir, TAG_VERSION_PATTERN)
        self.assertEqual(want_tags, have_tags)

        got = check_subprocess(f'git -C "{test_dir}" remote -v')

        # Upstream repo is configured for pulls, but not for pushes.
        self.assertEqual(
            "\n".join(
                [
                    f"origin\t{origin_dir} (fetch)",
                    f"origin\t{origin_dir} (push)",
                    f"upstream\t{self.git_dir} (fetch)",
                    "upstream\tdisabled (push)",
                ]
            ),
            got,
        )

        reference = git.determine_git_repository_spec(test_dir)
        expect = GitRepositorySpec(
            os.path.basename(origin_dir),
            upstream=self.git_dir,
            origin=origin_dir,
            git_dir=test_dir,
        )
        self.assertEqual(expect, reference)

    def test_clone_branch(self):
        test_parent = os.path.join(self.base_temp_dir, "test_clone_branch")
        os.makedirs(test_parent)

        test_dir = os.path.join(test_parent, TEST_REPO_NAME)
        repository = GitRepositorySpec(
            TEST_REPO_NAME, git_dir=test_dir, origin=self.git_dir
        )
        self.git.clone_repository_to_path(repository, branch=BRANCH_A)
        self.assertEqual(BRANCH_A, self.git.query_local_repository_branch(test_dir))

    def test_branch_not_found_exception(self):
        test_parent = os.path.join(self.base_temp_dir, "test_bad_branch")
        os.makedirs(test_parent)
        test_dir = os.path.join(test_parent, TEST_REPO_NAME)
        self.assertFalse(os.path.exists(test_dir))

        repository = GitRepositorySpec(
            TEST_REPO_NAME, git_dir=test_dir, origin=self.git_dir
        )

        branch = "Bogus"
        regexp = r"Branches \['{branch}'\] do not exist in {url}\.".format(
            branch=branch, url=self.git_dir
        )
        with self.assertRaisesRegex(Exception, regexp):
            self.git.clone_repository_to_path(repository, branch=branch)
        self.assertFalse(os.path.exists(test_dir))

    def test_clone_failure(self):
        test_dir = os.path.join(self.base_temp_dir, "clone_failure", TEST_REPO_NAME)
        os.makedirs(test_dir)
        with open(os.path.join(test_dir, "something"), "w") as f:
            f.write("not empty")

        repository = GitRepositorySpec(
            TEST_REPO_NAME, git_dir=test_dir, origin=self.git_dir
        )
        regexp = ".* clone .*"
        with self.assertRaisesRegex(Exception, regexp):
            self.git.clone_repository_to_path(repository, branch="master")

    def test_default_branch(self):
        test_parent = os.path.join(self.base_temp_dir, "test_default_branch")
        os.makedirs(test_parent)
        test_dir = os.path.join(test_parent, TEST_REPO_NAME)

        repository = GitRepositorySpec(
            TEST_REPO_NAME, git_dir=test_dir, origin=self.git_dir
        )
        self.git.clone_repository_to_path(
            repository, branch="Bogus", default_branch=BRANCH_B
        )
        self.assertEqual(BRANCH_B, self.git.query_local_repository_branch(test_dir))

    def test_commit_at_tag(self):
        self.run_git("checkout " + VERSION_A)
        want = self.git.query_local_repository_commit_id(self.git_dir)
        self.run_git("checkout master")
        self.assertEqual(want, self.git.query_commit_at_tag(self.git_dir, VERSION_A))
        self.assertIsNone(self.git.query_commit_at_tag(self.git_dir, "BogusTag"))

    def test_summarize(self):
        # All the tags in this fixture are where the head is tagged, so
        # these are not that interesting. This is tested again in the
        # CommitMessage fixture for more interesting cases.
        tests = [(BRANCH_BASE, VERSION_BASE), (BRANCH_A, VERSION_A)]
        for branch, tag in tests:
            self.run_git("checkout " + branch)
            summary = self.git.collect_repository_summary(self.git_dir)
            self.assertEqual(
                self.git.query_local_repository_commit_id(self.git_dir),
                summary.commit_id,
            )
            self.assertEqual(tag, summary.tag)
            self.assertEqual(tag.split("v")[1], summary.version)
            self.assertEqual([], summary.commit_messages)


class TestSemanticVersion(unittest.TestCase):
    def test_semver_make_valid(self):
        tests = [
            ("v1.0.0", SemanticVersion("v", 1, 0, 0)),
            ("v10.11.12", SemanticVersion("v", 10, 11, 12)),
        ]
        for tag, expect in tests:
            semver = SemanticVersion.make(tag)
            self.assertEqual(semver, expect)
            self.assertEqual(tag, semver.to_tag())
            self.assertEqual(tag[tag.rfind("v") + 1 :], semver.to_version())

    def test_semver_next(self):
        semver = SemanticVersion("A", 1, 2, 3)
        tests = [
            (SemanticVersion.TAG_INDEX, SemanticVersion("B", 1, 2, 3), None),
            (None, SemanticVersion("A", 1, 2, 3), None),
            (
                SemanticVersion.MAJOR_INDEX,
                SemanticVersion("A", 2, 2, 3),
                SemanticVersion("A", 2, 0, 0),
            ),  # next major index to semver
            (
                SemanticVersion.MINOR_INDEX,
                SemanticVersion("A", 1, 3, 3),
                SemanticVersion("A", 1, 3, 0),
            ),  # next minor index to semver
            (
                SemanticVersion.PATCH_INDEX,
                SemanticVersion("A", 1, 2, 4),
                SemanticVersion("A", 1, 2, 4),
            ),  # next patch index to semver
        ]
        for expect_index, test, next_semver in tests:
            self.assertEqual(expect_index, semver.most_significant_diff_index(test))
            self.assertEqual(expect_index, test.most_significant_diff_index(semver))
            if expect_index is not None and expect_index > SemanticVersion.TAG_INDEX:
                self.assertEqual(next_semver, semver.next(expect_index))

    def test_semver_sort(self):
        versions = [
            SemanticVersion.make("v1.9.7"),
            SemanticVersion.make("v9.8.7"),
            SemanticVersion.make("v11.0.0"),
            SemanticVersion.make("v3.10.2"),
            SemanticVersion.make("v3.0.4"),
            SemanticVersion.make("v3.2.2"),
            SemanticVersion.make("v3.2.0"),
            SemanticVersion.make("v3.2.1"),
        ]

        got = sorted(versions)
        expect = [
            SemanticVersion.make("v1.9.7"),
            SemanticVersion.make("v3.0.4"),
            SemanticVersion.make("v3.2.0"),
            SemanticVersion.make("v3.2.1"),
            SemanticVersion.make("v3.2.2"),
            SemanticVersion.make("v3.10.2"),
            SemanticVersion.make("v9.8.7"),
            SemanticVersion.make("v11.0.0"),
        ]
        self.assertEqual(expect, got)


class TestCommitMessage(unittest.TestCase):
    PATCH_BRANCH = "patch_branch"
    MINOR_BRANCH = "minor_branch"
    MAJOR_BRANCH = "major_branch"
    MERGED_BRANCH = "merged_branch"

    PATCH_MINOR_BRANCH = "patch_minor_branch"
    PATCH_MINOR_X = "marker_for_minor_x"

    @classmethod
    def run_git(cls, command):
        return check_subprocess(
            f'git -C "{cls.git_dir}" {command}'
        )

    @classmethod
    def setUpClass(cls):
        cls.git = GitRunner(make_default_options())
        cls.base_temp_dir = tempfile.mkdtemp(prefix="git_test")
        cls.git_dir = os.path.join(cls.base_temp_dir, "commit_message_test")
        os.makedirs(cls.git_dir)

        git_dir = cls.git_dir
        gitify = lambda args: f'git -C "{git_dir}" {args}'
        check_subprocess_sequence(
            [
                gitify("init"),
                f'touch "{git_dir}/base_file"',
                gitify(f'add "{git_dir}/base_file"'),
                gitify('commit -a -m "feat(test): added file"'),
                gitify(f"tag {VERSION_BASE} HEAD"),
                # For testing patches
                gitify(
                    f"checkout -b {cls.PATCH_BRANCH}"
                ),
                f'touch "{git_dir}/patch_file"',
                gitify(f'add "{git_dir}/patch_file"'),
                gitify('commit -a -m "fix(testA): added patch_file"'),
                # For testing minor versions
                gitify(
                    f"checkout -b {cls.MINOR_BRANCH}"
                ),
                f'touch "{git_dir}/minor_file"',
                gitify(f'add "{git_dir}/minor_file"'),
                gitify('commit -a -m "feat(testB): added minor_file"'),
                # For testing major versions
                gitify(
                    f"checkout -b {cls.MAJOR_BRANCH}"
                ),
                f'touch "{git_dir}/major_file"',
                gitify(f'add "{git_dir}/major_file"'),
                gitify(
                    "commit -a -m"
                    ' "feat(testC): added major_file\n'
                    "\nInterestingly enough, this is a BREAKING CHANGE."
                    '"'
                ),
                # For testing composite commits from a merge of commits
                gitify(
                    "checkout -b {merged_branch}".format(
                        merged_branch=cls.MERGED_BRANCH
                    )
                ),
                gitify("reset --hard HEAD~3"),
                gitify("merge --squash HEAD@{1}"),
            ]
        )

        env = dict(os.environ)
        if os.path.exists("/bin/true"):
            env["EDITOR"] = "/bin/true"
        elif os.path.exists("/usr/bin/true"):
            env["EDITOR"] = "/usr/bin/true"
        else:
            raise NotImplementedError("platform not supported for this test")
        check_subprocess(f'git -C "{git_dir}" commit', env=env)

        # For testing changelog from a commit
        check_subprocess_sequence(
            [
                gitify(f"checkout {cls.MINOR_BRANCH}"),
                gitify(
                    f"checkout -b {cls.PATCH_MINOR_BRANCH}"
                ),
                f'touch "{git_dir}/xbefore_file"',
                gitify(f'add "{git_dir}/xbefore_file"'),
                gitify('commit -a -m "feat(test): COMMIT AT TAG"'),
                gitify(f"tag {cls.PATCH_MINOR_X} HEAD"),
                f'touch "{git_dir}/x_first"',
                gitify(f'add "{git_dir}/x_first"'),
                gitify('commit -a -m "fix(test): First Fix"'),
                f'rm "{git_dir}/x_first"',
                gitify('commit -a -m "fix(test): Second Fix"'),
            ]
        )

    def test_summarize(self):
        # patch, minor, major branches haven't been tagged so all should return
        # last tag 'v0.1.0' regardless of additional commits since this tag.
        all_tests = [
            (self.PATCH_BRANCH, "0.1.0"),
            (self.MINOR_BRANCH, "0.1.0"),
            (self.MAJOR_BRANCH, "0.1.0"),
        ]
        for _, spec in enumerate(all_tests):
            branch, version = spec
            # CommitMessage fixture for more interesting cases.
            self.run_git("checkout " + branch)
            summary = self.git.collect_repository_summary(self.git_dir)
            self.assertEqual("v" + version, summary.tag)
            self.assertEqual(version, summary.version)
            clean_messages = [
                "\n".join([line.strip() for line in lines])
                for lines in [m.message.split("\n") for m in summary.commit_messages]
            ]
            # because there has been no tag there are also no messages.
            self.assertEqual([], clean_messages)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base_temp_dir)

    def setUp(self):
        self.run_git(f"checkout master")

    def test_message_analysis_with_commit_baseline(self):
        # pylint: disable=line-too-long
        git = self.git

        tests = [(self.PATCH_MINOR_BRANCH, self.PATCH_MINOR_X)]
        for branch, baseline_commit in tests:
            self.run_git(f"checkout {branch}")
            commit_id = git.query_local_repository_commit_id(self.git_dir)
            messages = git.query_local_repository_commits_between_two_commit_ids(
                self.git_dir, baseline_commit, commit_id
            )
            self.assertEqual(2, len(messages))
            self.assertEqual("fix(test): Second Fix", messages[0].message.strip())
            self.assertEqual("fix(test): First Fix", messages[1].message.strip())


class TestRepositorySummary(unittest.TestCase):
    def test_to_yaml(self):
        summary = RepositorySummary(
            "abcd1234",
            "mytag-987",
            "0.0.1",
            [CommitMessage("commit-abc", "author", "date", "commit message")],
        )

        expect = """commit_id: {id}
tag: {tag}
version: {version}
""".format(
            id=summary.commit_id, tag=summary.tag, version=summary.version
        )
        self.assertEqual(expect, summary.to_yaml(with_commit_messages=False))

    def test_yamilfy(self):
        # The summary values are arbitrary. Just verifying we can go in and out
        # of yaml.
        summary = RepositorySummary(
            "abcd",
            "tag-123",
            "1.2.0",
            [
                CommitMessage("commitB", "authorB", "dateB", "messageB"),
                CommitMessage("commitA", "authorA", "dateA", "messageA"),
            ],
        )
        yamlized = yaml.safe_load(summary.to_yaml())
        self.assertEqual(summary.commit_id, yamlized["commit_id"])
        self.assertEqual(summary.tag, yamlized["tag"])
        self.assertEqual(summary.version, yamlized["version"])
        self.assertEqual(
            [
                {
                    "commit_id": "commit" + x,
                    "author": "author" + x,
                    "date": "date" + x,
                    "message": "message" + x,
                }
                for x in ["B", "A"]
            ],
            yamlized["commit_messages"],
        )
        self.assertEqual(summary, RepositorySummary.from_dict(yamlized))


if __name__ == "__main__":
    init_runtime()
    unittest.main(verbosity=2)
