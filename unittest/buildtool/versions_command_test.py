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
from unittest.mock import patch
import yaml

from test_util import init_runtime, BaseTestFixture

from buildtool import SPINNAKER_HALYARD_GCS_BUCKET_NAME, SPINNAKER_CHANGELOG_BASE_URL

import buildtool.__main__ as bomtool_main
import buildtool.versions_commands

from buildtool.versions_commands import (
    VersionsBuilder,
    PublishVersionsCommand,
    MAXIMUM_SPINNAKER_RELEASE_COUNT,
)


class TestVersionsBuilder(BaseTestFixture):
    """Test Version Builder."""

    def setUp(self):
        super().setUp()
        self.test_root = os.path.join(self.base_temp_dir, self._testMethodName)

    def test_build_single_version_dict(self):
        """A single release version"""
        builder = VersionsBuilder()

        with patch("buildtool.versions_commands.now") as mock_now:
            mock_now.return_value = 1234567890000
            got = builder.build_single_release_dict(
                "1.29.0",
                "1.50.0",
            )

        expected = {
            "alias": "v1.29.0",
            "changelog": f"{SPINNAKER_CHANGELOG_BASE_URL}/1.29.0-changelog/",
            "lastUpdate": 1234567890000,
            "minimumHalyardVersion": "1.50.0",
            "version": "1.29.0",
        }
        self.assertDictEqual(expected, got)

    def test_get_major_minor_patch_version(self):
        """A version string should get split into an array of strings: major, minor, patch."""
        version = "1.2.3"
        expected = ["1", "2", "3"]
        builder = VersionsBuilder()
        got = builder.get_major_minor_patch_version(version)
        self.assertListEqual(expected, got)

    def test_add_or_replace_release_new_minor(self):
        """A new minor version should be added to versions.yml and the list of
        versions should increase by one."""
        builder = VersionsBuilder()

        release_100 = builder.build_single_release_dict("1.0.0", "1.50.0")
        release_110 = builder.build_single_release_dict("1.1.0", "1.50.0")
        release_120 = builder.build_single_release_dict("1.2.0", "1.50.0")
        releases = [release_100, release_110, release_120]

        release_140 = builder.build_single_release_dict("1.4.0", "1.50.0")

        got = builder.add_or_replace_release(release_140, releases)

        self.assertEqual(len(got), 4)  # count of releases has increased
        self.assertDictEqual(got[-1], release_140)

    def test_add_or_replace_release_new_patch(self):
        """A new patch version should replace existing in versions.yml and
        there should remain our accepted maximum versions in the list."""
        builder = VersionsBuilder()

        release_100 = builder.build_single_release_dict("1.0.0", "1.50.0")
        release_110 = builder.build_single_release_dict("1.1.0", "1.50.0")
        release_120 = builder.build_single_release_dict("1.2.0", "1.50.0")
        releases = [release_100, release_110, release_120]

        release_111 = builder.build_single_release_dict("1.1.1", "1.50.0")

        got = builder.add_or_replace_release(release_111, releases)

        self.assertEqual(len(got), 3)  # count of releases has not increased
        self.assertDictEqual(got[1], release_111)  # replaced 1.1.0 at index '0'

    def test_add_or_replace_release_minor_already_exists(self):
        """Adding a duplicate new version should error."""
        builder = VersionsBuilder()

        release_100 = builder.build_single_release_dict("1.0.0", "1.50.0")
        release_110 = builder.build_single_release_dict("1.1.0", "1.50.0")
        release_120 = builder.build_single_release_dict("1.2.0", "1.50.0")

        releases = [release_100, release_110, release_120]

        with self.assertRaises(ValueError):
            builder.add_or_replace_release(release_120, releases)

    def test_build_new_version_with_default_base(self):
        """Adding a new version to default base_versions_dict should update all of the fields."""

        builder = VersionsBuilder()

        spinnaker_version = "1.29.0"
        minimum_halyard_version = "1.75.0"
        latest_halyard_version = "1.99.0"
        got = builder.build(
            spinnaker_version,
            minimum_halyard_version,
            latest_halyard_version,
        )

        self.assertEqual(len(got["versions"]), 1)
        self.assertEqual(got["latestSpinnaker"], spinnaker_version)
        self.assertEqual(got["latestHalyard"], latest_halyard_version)

    def test_build_new_version_latest_halyard_unchanged(self):
        """Adding a new version should update all of the fields."""

        path = os.path.join(os.path.dirname(__file__), "standard_test_versions.yml")
        with open(path, encoding="utf-8") as f:
            versions_yaml_string = f.read()
        versions_dict = yaml.safe_load(versions_yaml_string)

        builder = VersionsBuilder.new_from_versions(versions_dict)

        spinnaker_version = "1.29.0"
        minimum_halyard_version = "1.75.0"
        latest_halyard_version = "1.99.0"
        got = builder.build(
            spinnaker_version,
            minimum_halyard_version,
            latest_halyard_version,
        )

        self.assertEqual(len(got["versions"]), MAXIMUM_SPINNAKER_RELEASE_COUNT)
        self.assertEqual(got["latestSpinnaker"], spinnaker_version)
        self.assertEqual(got["latestHalyard"], latest_halyard_version)

    def test_build_new_version_latest_halyard_changed(self):
        """Adding a new version should update all of the fields."""

        path = os.path.join(os.path.dirname(__file__), "standard_test_versions.yml")
        with open(path, encoding="utf-8") as f:
            versions_yaml_string = f.read()
        versions_dict = yaml.safe_load(versions_yaml_string)

        builder = VersionsBuilder.new_from_versions(versions_dict)

        spinnaker_version = "1.27.2"
        minimum_halyard_version = "1.46"
        got = builder.build(
            spinnaker_version,
            minimum_halyard_version,
        )

        self.assertEqual(len(got["versions"]), MAXIMUM_SPINNAKER_RELEASE_COUNT)
        self.assertEqual(got["latestSpinnaker"], "1.28.1")  # standard_test_versions.yml
        self.assertEqual(got["latestHalyard"], "1.49.0")  # standard_test_versions.yml


class TestUpdateVersionsCommand(BaseTestFixture):
    """Test update_versions command."""

    def setUp(self):
        super().setUp()
        self.parser = argparse.ArgumentParser()
        self.subparsers = self.parser.add_subparsers()

    def test_default_update_versions_options(self):
        """Test update_versions default argument options"""
        registry = {}
        buildtool.versions_commands.register_commands(registry, self.subparsers, {})
        self.assertTrue("update_versions" in registry)

        options = self.parser.parse_args(["update_versions"])
        option_dict = vars(options)

        for key in [
            "versions_yml_path",
            "latest_halyard_version",
            "spinnaker_version",
            "minimum_halyard_version",
        ]:
            self.assertIsNone(option_dict[key])

    def test_update_versions(self):
        """Fail updating versions.yml if the new version is an older patch
        version"""

        options = self.options
        options.versions_yml_path = os.path.join(
            os.path.dirname(__file__), "standard_test_versions.yml"
        )
        options.spinnaker_version = "1.29.0"  # not in standard_test_versions.yml
        options.minimum_halyard_version = "1.99.1"
        options.latest_halyard_version = None

        defaults = vars(self.options)
        parser = argparse.ArgumentParser()
        registry = bomtool_main.make_registry(
            [buildtool.versions_commands], parser, defaults
        )
        bomtool_main.add_standard_parser_args(parser, defaults)
        factory = registry["update_versions"]
        command = factory.make_command(options)

        command()


class TestPublishVersionsCommand(BaseTestFixture):
    """Test publish_versions command."""

    def setUp(self):
        super().setUp()
        self.parser = argparse.ArgumentParser()
        self.subparsers = self.parser.add_subparsers()

    def test_default_publish_versions_options(self):
        """Test publish_versions default argument options"""
        registry = {}
        buildtool.versions_commands.register_commands(registry, self.subparsers, {})
        self.assertTrue("publish_versions" in registry)

        options = self.parser.parse_args(["publish_versions"])
        option_dict = vars(options)

        self.assertEqual(True, options.dry_run)

        self.assertIsNone(option_dict["versions_yml_path"])

    def test_publish_versions_dry_run(self):
        """Test publish_versions with dry_run enabled.
        gcs_upload function should not be called."""

        options = self.options

        options.versions_yml_path = os.path.join(
            os.path.dirname(__file__), "standard_test_versions.yml"
        )
        options.dry_run = True

        mock_gcs_upload = self.patch_method(PublishVersionsCommand, "gcs_upload")

        defaults = vars(self.options)
        parser = argparse.ArgumentParser()
        registry = bomtool_main.make_registry(
            [buildtool.versions_commands], parser, defaults
        )
        bomtool_main.add_standard_parser_args(parser, defaults)
        factory = registry["publish_versions"]
        command = factory.make_command(options)
        command()

        self.assertEqual(0, mock_gcs_upload.call_count)

    def test_publish_versions(self):
        """Test publish_versions with dry_run disabled.
        Verify mocked gsutil command is called with test versions.yml file
        path and GCS Bucket url."""

        options = self.options

        options.versions_yml_path = os.path.join(
            os.path.dirname(__file__), "standard_test_versions.yml"
        )
        options.dry_run = False

        mock_gcs_upload = self.patch_method(PublishVersionsCommand, "gcs_upload")

        defaults = vars(self.options)
        parser = argparse.ArgumentParser()
        registry = bomtool_main.make_registry(
            [buildtool.versions_commands], parser, defaults
        )
        bomtool_main.add_standard_parser_args(parser, defaults)
        factory = registry["publish_versions"]
        command = factory.make_command(options)
        command()

        # dry run disabled, upload versions.yml file (path on disk) to GCS bucket
        mock_gcs_upload.assert_called_once_with(
            options.versions_yml_path,
            f"gs://{SPINNAKER_HALYARD_GCS_BUCKET_NAME}/versions.yml",
        )


if __name__ == "__main__":
    init_runtime()
    unittest.main(verbosity=2)
