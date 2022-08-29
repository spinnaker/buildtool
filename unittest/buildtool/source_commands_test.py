# Copyright 2022 Salesforce.com, Inc.
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

import argparse
import os
import unittest

from test_util import (
    BASE_VERSION_TAG,
    NORMAL_REPO,
    NORMAL_SERVICE,
    UNTAGGED_BRANCH,
    PATCH_BRANCH,
    init_runtime,
    BaseGitRepoTestFixture,
)

import buildtool.__main__ as buildtool_main
import buildtool.source_commands
from buildtool import GitRunner
from buildtool import check_subprocess_sequence

NEW_MINOR_TAG = "v7.9.0"
NEW_PATCH_TAG = "v7.8.10"


class TestSourceCommandFixture(BaseGitRepoTestFixture):
    def setUp(self):
        super().setUp()
        self.parser = argparse.ArgumentParser()
        self.subparsers = self.parser.add_subparsers(title="command", dest="command")

    def test_new_release_branch_command(self):
        """assert new release branch creation:
        when source branch HEAD is tagged, successfully branch at latest tag (HEAD)
        """

        defaults = {
            "input_dir": self.options.input_dir,
            "output_dir": self.options.output_dir,
            "only_repositories": NORMAL_SERVICE,
            "github_owner": "default",
            "git_branch": "master",  # tagged at HEAD
            "new_branch": "release-0.1.x",
            "github_repository_root": self.options.github_repository_root,
        }

        registry = {}
        buildtool_main.add_standard_parser_args(self.parser, defaults)
        buildtool.source_commands.register_commands(registry, self.subparsers, defaults)

        factory = registry["new_release_branch"]
        factory.init_argparser(self.parser, defaults)

        options = self.parser.parse_args(["new_release_branch"])

        mock_push_branch = self.patch_method(GitRunner, "push_branch_to_origin")

        command = factory.make_command(options)
        command()

        base_git_dir = os.path.join(options.input_dir, "new_release_branch")
        self.assertEqual(os.listdir(base_git_dir), [NORMAL_SERVICE])
        git_dir = os.path.join(base_git_dir, NORMAL_SERVICE)

        # new 'release' branch HEAD should match 'master' branch HEAD
        self.assertEqual(
            GitRunner(options).query_local_repository_commit_id(git_dir),
            self.repo_commit_map[NORMAL_SERVICE]["master"],
        )

        # new 'release' branch should have tag
        self.assertIsNotNone(
            GitRunner(options).query_commit_at_tag(git_dir, BASE_VERSION_TAG)
        )

        mock_push_branch.assert_called_once_with(git_dir, "release-0.1.x")

    def test_new_release_branch_with_commits_command(self):
        """assert new release branch creation:
        when source branch HEAD is not tagged but a tag exists, successfully branch at latest tag
        """

        defaults = {
            "input_dir": self.options.input_dir,
            "output_dir": self.options.output_dir,
            "only_repositories": NORMAL_SERVICE,
            "github_owner": "default",
            "git_branch": PATCH_BRANCH,  # this branch has commits since last tag
            "new_branch": "release-0.1.x",
            "github_repository_root": self.options.github_repository_root,
        }

        registry = {}
        buildtool_main.add_standard_parser_args(self.parser, defaults)
        buildtool.source_commands.register_commands(registry, self.subparsers, defaults)

        factory = registry["new_release_branch"]
        factory.init_argparser(self.parser, defaults)

        options = self.parser.parse_args(["new_release_branch"])

        mock_push_branch = self.patch_method(GitRunner, "push_branch_to_origin")

        command = factory.make_command(options)
        command()

        base_git_dir = os.path.join(options.input_dir, "new_release_branch")
        self.assertEqual(os.listdir(base_git_dir), [NORMAL_SERVICE])
        git_dir = os.path.join(base_git_dir, NORMAL_SERVICE)

        # new 'release' branch HEAD should match 'master' branch HEAD (which is tagged)
        self.assertEqual(
            GitRunner(options).query_local_repository_commit_id(git_dir),
            self.repo_commit_map[NORMAL_SERVICE]["master"],
        )

        # new 'release' branch should have tag
        self.assertIsNotNone(
            GitRunner(options).query_commit_at_tag(git_dir, BASE_VERSION_TAG)
        )

        mock_push_branch.assert_called_once_with(git_dir, "release-0.1.x")

    @unittest.expectedFailure
    def test_new_release_branch_exists_command(self):
        """assert new release branch creation:
        when destination branch already exists should fail
        """

        defaults = {
            "input_dir": self.options.input_dir,
            "output_dir": self.options.output_dir,
            "only_repositories": NORMAL_SERVICE,
            "github_owner": "default",
            "git_branch": PATCH_BRANCH,  # this branch has commits since last tag
            "new_branch": PATCH_BRANCH,  # same as source, i.e: already exists
            "github_repository_root": self.options.github_repository_root,
        }

        registry = {}
        buildtool_main.add_standard_parser_args(self.parser, defaults)
        buildtool.source_commands.register_commands(registry, self.subparsers, defaults)

        factory = registry["new_release_branch"]
        factory.init_argparser(self.parser, defaults)

        options = self.parser.parse_args(["new_release_branch"])

        command = factory.make_command(options)
        command()

    def test_tag_branch_master_command(self):
        """assert tagging behaviour on master branch:
        1. when tagged at HEAD a new tag should not generated and pushed
        2. when NOT tagged at HEAD a new minor tag should be generated and pushed
        """

        defaults = {
            "input_dir": self.options.input_dir,
            "output_dir": self.options.output_dir,
            "only_repositories": NORMAL_REPO,
            "github_owner": "default",
            "git_branch": "master",
            "github_repository_root": self.options.github_repository_root,
        }

        registry = {}
        buildtool_main.add_standard_parser_args(self.parser, defaults)
        buildtool.source_commands.register_commands(registry, self.subparsers, defaults)

        factory = registry["tag_branch"]
        factory.init_argparser(self.parser, defaults)

        options = self.parser.parse_args(["tag_branch"])

        mock_push_tag = self.patch_method(GitRunner, "push_tag_to_origin")

        command = factory.make_command(options)
        command()

        base_git_dir = os.path.join(options.input_dir, "tag_branch")
        self.assertEqual(os.listdir(base_git_dir), [NORMAL_REPO])
        git_dir = os.path.join(base_git_dir, NORMAL_REPO)

        head_commit_id = GitRunner(options).query_local_repository_commit_id(git_dir)

        latest_tag, _ = GitRunner(options).find_newest_tag_and_common_commit_from_id(
            git_dir, head_commit_id
        )

        # master branch already tagged at HEAD so no new tag should be added
        # and pushed.
        self.assertEqual(BASE_VERSION_TAG, latest_tag)
        self.assertEqual(0, mock_push_tag.call_count)

        # now add a commit to master branch and validate a tag is generated.

        check_subprocess_sequence(
            [
                f"touch  {NORMAL_REPO}-basefile-2.txt",
                f"git add {NORMAL_REPO}-basefile-2.txt",
                'git commit -a -m "feat(second): second commit"',
            ],
            cwd=git_dir,
        )

        command()

        head_commit_id_2 = GitRunner(options).query_local_repository_commit_id(git_dir)
        latest_tag_2, _ = GitRunner(options).find_newest_tag_and_common_commit_from_id(
            git_dir, head_commit_id_2
        )

        # master branch not tagged at HEAD so a new minor tag should be added
        # and pushed.
        self.assertEqual(NEW_MINOR_TAG, latest_tag_2)
        self.assertEqual(1, mock_push_tag.call_count)

    def test_tag_branch_untagged_command(self):
        """assert tagging behaviour on non-master branches:
        1. when NOT tagged at HEAD a new patch tag should be generated and pushed
        2. when tagged at HEAD a new tag should not generated and pushed
        """

        defaults = {
            "input_dir": self.options.input_dir,
            "output_dir": self.options.output_dir,
            "only_repositories": NORMAL_REPO,
            "github_owner": "default",
            "git_branch": UNTAGGED_BRANCH,
            "github_repository_root": self.options.github_repository_root,
        }

        registry = {}
        buildtool_main.add_standard_parser_args(self.parser, defaults)
        buildtool.source_commands.register_commands(registry, self.subparsers, defaults)

        factory = registry["tag_branch"]
        factory.init_argparser(self.parser, defaults)

        options = self.parser.parse_args(["tag_branch"])

        mock_push_tag = self.patch_method(GitRunner, "push_tag_to_origin")

        command = factory.make_command(options)
        command()

        base_git_dir = os.path.join(options.input_dir, "tag_branch")
        self.assertEqual(os.listdir(base_git_dir), [NORMAL_REPO])
        git_dir = os.path.join(base_git_dir, NORMAL_REPO)

        head_commit_id = GitRunner(options).query_local_repository_commit_id(git_dir)

        latest_tag, _ = GitRunner(options).find_newest_tag_and_common_commit_from_id(
            git_dir, head_commit_id
        )

        # non-master branch not tagged at HEAD so a new patch tag should be
        # added and pushed.
        self.assertEqual(NEW_PATCH_TAG, latest_tag)
        self.assertEqual(1, mock_push_tag.call_count)

        # run command again and confirm that no new tag was added as we haven't
        # added any commits.

        command()
        head_commit_id_2 = GitRunner(options).query_local_repository_commit_id(git_dir)
        latest_tag_2, _ = GitRunner(options).find_newest_tag_and_common_commit_from_id(
            git_dir, head_commit_id_2
        )

        # non-master branch already tagged at HEAD so no new tag should be
        # added and pushed.
        self.assertEqual(NEW_PATCH_TAG, latest_tag_2)
        self.assertEqual(1, mock_push_tag.call_count)


if __name__ == "__main__":
    init_runtime()
    unittest.main(verbosity=2)
