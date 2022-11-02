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

import argparse
import datetime
import os
import tempfile
import textwrap
import unittest
from unittest.mock import patch

import yaml

from buildtool import (
    DEFAULT_BUILD_NUMBER,
    SPINNAKER_DEBIAN_REPOSITORY,
    SPINNAKER_DOCKER_REGISTRY,
    SPINNAKER_GOOGLE_IMAGE_PROJECT,
    BranchSourceCodeManager,
    GitRepositorySpec,
    MetricsManager,
    RepositorySummary,
    SourceInfo,
)
import buildtool

import buildtool.__main__ as bomtool_main
import buildtool.bom_commands
from buildtool.bom_commands import BomBuilder, BuildBomCommand, PublishBomCommand


from test_util import (
    ALL_STANDARD_TEST_BOM_REPO_NAMES,
    PATCH_BRANCH,
    BASE_VERSION_NUMBER,
    NORMAL_REPO,
    NORMAL_SERVICE,
    OUTLIER_REPO,
    OUTLIER_SERVICE,
    BaseGitRepoTestFixture,
    BaseTestFixture,
    init_runtime,
)


def load_default_bom_dependencies():
    path = os.path.join(
        os.path.dirname(__file__), "../../dev/buildtool/bom_dependencies.yml"
    )
    with open(path) as stream:
        return yaml.safe_load(stream.read())


def make_default_options(options):
    options.git_branch = "OptionBranch"
    options.github_hostname = "test-hostname"
    options.github_owner = "test-user"
    options.bom_dependencies_path = None
    options.build_number = "OptionBuildNumber"
    options.github_upstream_owner = "spinnaker"
    return options


class TestBuildBomCommand(BaseGitRepoTestFixture):
    def setUp(self):
        super().setUp()
        self.parser = argparse.ArgumentParser()
        self.subparsers = self.parser.add_subparsers()

    def make_test_options(self):
        options = super().make_test_options()
        return make_default_options(options)

    def test_default_bom_options(self):
        registry = {}
        buildtool.bom_commands.register_commands(registry, self.subparsers, {})
        self.assertTrue("build_bom" in registry)

        options = self.parser.parse_args(["build_bom"])
        option_dict = vars(options)
        self.assertEqual(DEFAULT_BUILD_NUMBER, options.build_number)

        for key in ["bom_path", "github_owner"]:
            self.assertIsNone(option_dict[key])

    def test_bom_option_default_overrides(self):
        defaults = {"not_used": False}
        defaults.update(vars(self.options))

        registry = {}
        buildtool.bom_commands.register_commands(registry, self.subparsers, defaults)
        parsed_options = self.parser.parse_args(["build_bom"])
        parsed_option_dict = vars(parsed_options)

        self.assertTrue("not_used" not in parsed_option_dict)
        for key, value in defaults.items():
            if key in ["not_used", "command", "input_dir", "output_dir"]:
                continue
            self.assertEqual(value, parsed_option_dict[key])

    def test_bom_command(self):
        """Make sure when we run "build_bom" we actually get what we meant."""
        defaults = vars(make_default_options(self.options))
        defaults.update(
            {
                "bom_path": "MY PATH",
                "github_owner": "TestOwner",
                "input_dir": "TestInputRoot",
            }
        )
        del defaults["github_repository_root"]
        parser = argparse.ArgumentParser()
        registry = bomtool_main.make_registry(
            [buildtool.bom_commands], parser, defaults
        )
        bomtool_main.add_standard_parser_args(parser, defaults)
        options = parser.parse_args(["build_bom"])

        prefix = "http://test-domain.com/test-owner"

        make_fake = self.patch_method

        # When asked to filter the normal bom repos to determine source_repositories
        # we'll return our own fake repository as if we configured the original
        # command for it. This will also make it easier to test just the one
        # repo rather than all, and that there are no assumptions.
        mock_filter = make_fake(BuildBomCommand, "filter_repositories")
        test_repository = GitRepositorySpec(
            "clouddriver", commit_id="CommitA", origin=prefix + "/TestRepoA"
        )
        mock_filter.return_value = [test_repository]

        # When the base command ensures the local repository exists, we'll
        # intercept that call and do nothing rather than the git checkouts, etc.
        make_fake(BranchSourceCodeManager, "ensure_local_repository")

        # When the base command asks for the repository metadata, we'll return
        # this hardcoded info, then look for it later in the generated om.
        mock_refresh = make_fake(BranchSourceCodeManager, "refresh_source_info")
        summary = RepositorySummary("CommitA", "TagA", "9.8.7", [])
        source_info = SourceInfo("MyBuildNumber", summary)
        mock_refresh.return_value = source_info

        # When asked to write the bom out, do nothing.
        # We'll verify the bom later when looking at the mock call sequencing.
        mock_write = self.patch_function("buildtool.bom_commands.write_to_path")

        mock_now = self.patch_function("buildtool.bom_commands.now")
        mock_now.return_value = datetime.datetime(2018, 1, 2, 3, 4, 5)

        factory = registry["build_bom"]
        command = factory.make_command(options)
        command()

        # Verify source repositories were filtered
        self.assertEqual([test_repository], command.source_repositories)

        # Verify that the filter was called with the original bom repos,
        # and these repos were coming from the configured github_owner's repo.
        bom_repo_list = [
            GitRepositorySpec(
                name,
                git_dir=os.path.join("TestInputRoot", "build_bom", name),
                origin=f"https://{options.github_hostname}/TestOwner/{name}",
                upstream="https://github.com/spinnaker/" + name,
            )
            for name in sorted(
                [
                    "clouddriver",
                    "deck",
                    "echo",
                    "fiat",
                    "front50",
                    "gate",
                    "igor",
                    "kayenta",
                    "orca",
                    "rosco",
                    "spinnaker-monitoring",
                ]
            )
        ]
        mock_filter.assert_called_once_with(bom_repo_list)
        mock_refresh.assert_called_once_with(test_repository, "OptionBuildNumber")
        bom_text, bom_path = mock_write.call_args_list[0][0]

        self.assertEqual(bom_path, "MY PATH")
        bom = yaml.safe_load(bom_text)

        golden_text = (
            textwrap.dedent(
                """\
        artifactSources:
          gitPrefix: http://test-domain.com/test-owner
          debianRepository: %s
          dockerRegistry: %s
          googleImageProject: %s
        dependencies:
        services:
          clouddriver:
            commit: CommitA
            version: 9.8.7
        timestamp: '2018-01-02 03:04:05'
        version: OptionBuildNumber
    """
            )
            % (
                SPINNAKER_DEBIAN_REPOSITORY,
                SPINNAKER_DOCKER_REGISTRY,
                SPINNAKER_GOOGLE_IMAGE_PROJECT,
            )
        )
        golden_bom = yaml.safe_load(golden_text.format())
        golden_bom["dependencies"] = load_default_bom_dependencies()

        for key, value in golden_bom.items():
            self.assertEqual(value, bom[key])


class TestBomBuilder(BaseGitRepoTestFixture):
    def make_test_options(self):
        options = super().make_test_options()
        return make_default_options(options)

    def setUp(self):
        super().setUp()
        self.test_root = os.path.join(self.base_temp_dir, self._testMethodName)
        self.scm = BranchSourceCodeManager(self.options, self.test_root)

    def test_default_build(self):
        builder = BomBuilder(self.options, self.scm, MetricsManager.singleton())
        bom = builder.build()
        self.assertEqual(bom["dependencies"], load_default_bom_dependencies())

        # There are no services because we never added any.
        # Although the builder takes an SCM, you still need to explicitly add repos.
        self.assertEqual({}, bom["services"])

    def test_inject_dependencies(self):
        dependencies = {
            "DependencyA": {"version": "vA"},
            "DependencyB": {"version": "vB"},
        }
        fd, path = tempfile.mkstemp(prefix="bomdeps")
        os.close(fd)
        with open(path, "w") as stream:
            yaml.safe_dump(dependencies, stream)

        options = self.options
        options.bom_dependencies_path = path

        try:
            builder = BomBuilder(options, self.scm, MetricsManager.singleton())
            bom = builder.build()
        finally:
            os.remove(path)

        self.assertEqual(dependencies, bom["dependencies"])
        self.assertEqual({}, bom["services"])

    def test_build(self):
        test_root = self.test_root
        options = self.options
        options.git_branch = PATCH_BRANCH
        options.github_owner = "default"
        options.github_disable_upstream_push = True

        scm = BranchSourceCodeManager(options, test_root)
        golden_bom = dict(self.golden_bom)
        builder = BomBuilder.new_from_bom(
            options, scm, MetricsManager.singleton(), golden_bom
        )
        source_repositories = [
            scm.make_repository_spec(name) for name in ALL_STANDARD_TEST_BOM_REPO_NAMES
        ]
        for repository in source_repositories:
            scm.ensure_git_path(repository)
            summary = scm.git.collect_repository_summary(repository.git_dir)
            source_info = SourceInfo("SourceInfoBuildNumber", summary)
            builder.add_repository(repository, source_info)

        with patch("buildtool.bom_commands.now") as mock_now:
            mock_now.return_value = datetime.datetime(2018, 1, 2, 3, 4, 5)
            bom = builder.build()

        golden_bom["version"] = "OptionBuildNumber"
        golden_bom["timestamp"] = "2018-01-02 03:04:05"
        golden_bom["services"][NORMAL_SERVICE]["version"] = BASE_VERSION_NUMBER
        golden_bom["services"][OUTLIER_SERVICE]["version"] = BASE_VERSION_NUMBER
        golden_bom["services"]["monitoring-third-party"][
            "version"
        ] = BASE_VERSION_NUMBER

        golden_bom["artifactSources"] = {
            "gitPrefix": os.path.dirname(self.repo_commit_map[NORMAL_REPO]["ORIGIN"]),
            "debianRepository": SPINNAKER_DEBIAN_REPOSITORY,
            "dockerRegistry": SPINNAKER_DOCKER_REGISTRY,
            "googleImageProject": SPINNAKER_GOOGLE_IMAGE_PROJECT,
        }

        for key, value in bom["services"].items():
            # gate has extra commit on branch so commit id's should not match
            if key in ["gate", "monitoring-daemon", "monitoring-third-party"]:
                self.assertNotEqual(
                    value,
                    golden_bom["services"][key],
                    msg=f"key: {key} - value: {value}",
                )
            else:
                self.assertEqual(
                    value,
                    golden_bom["services"][key],
                    msg=f"key: {key} - value: {value}",
                )
        for key, value in bom.items():
            if key != "services":
                self.assertEqual(value, golden_bom[key])

    def test_rebuild(self):
        test_root = self.test_root
        options = self.options
        options.git_branch = "master"
        options.github_owner = "default"
        options.github_disable_upstream_push = True
        options.build_number = "UpdatedBuildNumber"

        scm = BranchSourceCodeManager(options, test_root)
        builder = BomBuilder.new_from_bom(
            options, scm, MetricsManager.singleton(), self.golden_bom
        )

        repository = scm.make_repository_spec(OUTLIER_REPO)
        scm.ensure_git_path(repository)
        scm.git.check_run(repository.git_dir, "checkout " + PATCH_BRANCH)
        summary = scm.git.collect_repository_summary(repository.git_dir)
        source_info = SourceInfo("SourceInfoBuildNumber", summary)
        builder.add_repository(repository, source_info)

        with patch("buildtool.bom_commands.now") as mock_now:
            mock_now.return_value = datetime.datetime(2018, 1, 2, 3, 4, 5)
            bom = builder.build()

        updated_service = bom["services"][OUTLIER_SERVICE]
        # OUTLIER_REPO hasn't been tagged since extra commits so bom should be same
        self.assertNotEqual(
            updated_service,
            {
                "commit": self.repo_commit_map[OUTLIER_REPO][PATCH_BRANCH],
                "version": BASE_VERSION_NUMBER,
            },
        )

        # The bom should be the same as before, but with new timestamp/version
        # and our service unchanged. And the artifactSources to our configs.
        updated_bom = dict(self.golden_bom)
        updated_bom["timestamp"] = "2018-01-02 03:04:05"
        updated_bom["version"] = "UpdatedBuildNumber"
        updated_bom["artifactSources"] = {
            "gitPrefix": self.golden_bom["artifactSources"]["gitPrefix"],
            "debianRepository": SPINNAKER_DEBIAN_REPOSITORY,
            "dockerRegistry": SPINNAKER_DOCKER_REGISTRY,
            "googleImageProject": SPINNAKER_GOOGLE_IMAGE_PROJECT,
        }

        for key, value in bom["services"].items():
            # monitoring-daemon has extra commit on branch so commit id's should not match
            if key in ["monitoring-daemon", "monitoring-third-party"]:
                self.assertNotEqual(
                    value,
                    updated_bom["services"][key],
                    msg=f"key: {key} - value: {value}",
                )
            else:
                self.assertEqual(
                    value,
                    updated_bom["services"][key],
                    msg=f"key: {key} - value: {value}",
                )
        for key, value in bom.items():
            if key != "services":
                self.assertEqual(value, updated_bom[key])

    def test_determine_most_common_prefix(self):
        options = self.options
        builder = BomBuilder(options, self.scm, MetricsManager.singleton())
        self.assertIsNone(builder.determine_most_common_prefix())

        prefix = ["http://github.com/one", "/local/source/path/two"]

        # Test two vs one in from different repo prefix
        # run the test twice changing the ordering the desired prefix is visible.
        for which in [0, 1]:
            repository = GitRepositorySpec(
                "RepoOne", origin=prefix[0] + "/RepoOne", commit_id="RepoOneCommit"
            )
            summary = RepositorySummary("RepoOneCommit", "RepoOneTag", "1.2.3", [])
            source_info = SourceInfo("BuildOne", summary)
            builder.add_repository(repository, source_info)
            self.assertEqual(prefix[0], builder.determine_most_common_prefix())

            repository = GitRepositorySpec(
                "RepoTwo", origin=prefix[which] + "/RepoTwo", commit_id="RepoTwoCommit"
            )
            summary = RepositorySummary("RepoTwoCommit", "RepoTwoTag", "2.2.3", [])
            source_info = SourceInfo("BuildTwo", summary)
            builder.add_repository(repository, source_info)

            repository = GitRepositorySpec(
                "RepoThree",
                origin=prefix[1] + "/RepoThree",
                commit_id="RepoThreeCommit",
            )
            summary = RepositorySummary("RepoThreeCommit", "RepoThreeTag", "3.2.0", [])
            source_info = SourceInfo("BuildThree", summary)
            builder.add_repository(repository, source_info)
            self.assertEqual(prefix[which], builder.determine_most_common_prefix())


class TestPublishBomCommand(BaseTestFixture):
    """Test publish_bom command."""

    def setUp(self):
        super().setUp()
        self.parser = argparse.ArgumentParser()
        self.subparsers = self.parser.add_subparsers()

    def test_default_bom_options(self):
        """Test publish_bom default argument options"""
        registry = {}
        buildtool.bom_commands.register_commands(registry, self.subparsers, {})
        self.assertTrue("publish_bom" in registry)

        options = self.parser.parse_args(["publish_bom"])
        option_dict = vars(options)

        self.assertEqual(True, options.dry_run)

        self.assertIsNone(option_dict["bom_path"])

    def test_publish_bom_dry_run(self):
        """Test publish_bom with dry_run enabled.
        gcs_upload function should not be called."""

        options = self.options

        options.bom_path = os.path.join(
            os.path.dirname(__file__), "standard_test_bom.yml"
        )
        options.dry_run = True

        mock_gcs_upload = self.patch_method(PublishBomCommand, "gcs_upload")

        defaults = vars(make_default_options(self.options))
        parser = argparse.ArgumentParser()
        registry = bomtool_main.make_registry(
            [buildtool.bom_commands], parser, defaults
        )
        bomtool_main.add_standard_parser_args(parser, defaults)
        factory = registry["publish_bom"]
        command = factory.make_command(options)
        command()

        self.assertEqual(0, mock_gcs_upload.call_count)

    def test_publish_bom(self):
        """Test publish_bom with dry_run disabled.
        Verify mocked gsutil command is called with test BOM file path and
        GCS Bucket url generated from the BOM's 'version' key."""

        options = self.options

        options.bom_path = os.path.join(
            os.path.dirname(__file__), "standard_test_bom.yml"
        )
        options.dry_run = False

        mock_gcs_upload = self.patch_method(PublishBomCommand, "gcs_upload")

        defaults = vars(make_default_options(self.options))
        parser = argparse.ArgumentParser()
        registry = bomtool_main.make_registry(
            [buildtool.bom_commands], parser, defaults
        )
        bomtool_main.add_standard_parser_args(parser, defaults)
        factory = registry["publish_bom"]
        command = factory.make_command(options)
        command()

        # dry run disabled, upload BOM file (path on disk) to GCS bucket at
        # version specified in BOM file 'version:' key.
        mock_gcs_upload.assert_called_once_with(
            options.bom_path, "gs://halconfig/bom/master-20181122334455.yml"
        )


if __name__ == "__main__":
    init_runtime()
    unittest.main(verbosity=2)
