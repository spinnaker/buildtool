# Copyright 2023 Karl Skewes. All Rights Reserved.
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
from unittest.mock import patch, call
import yaml

from test_util import init_runtime, BaseTestFixture

from buildtool import SPINNAKER_DOCKER_REGISTRY

import buildtool.__main__ as bomtool_main
import buildtool.container_commands

from buildtool.container_commands import TagContainersCommand


class TestTagContainersCommand(BaseTestFixture):
    """Test tag_containers command."""

    def setUp(self):
        super().setUp()
        self.parser = argparse.ArgumentParser()
        self.subparsers = self.parser.add_subparsers()

    def test_default_tag_containers_options(self):
        """Test tag_containers default argument options"""
        registry = {}
        buildtool.container_commands.register_commands(registry, self.subparsers, {})
        self.assertTrue("tag_containers" in registry)

        options = self.parser.parse_args(["tag_containers"])
        option_dict = vars(options)

        self.assertEqual(True, options.dry_run)

        self.assertIsNone(option_dict["bom_path"])
        self.assertIsNone(option_dict["spinnaker_version"])

    def test_tag_containers_dry_run(self):
        """Test tag_containers with dry_run enabled.
        regctl command should not be called."""

        options = self.options

        options.spinnaker_version = "1.2.3"
        options.bom_path = os.path.join(
            os.path.dirname(__file__), "standard_test_bom.yml"
        )
        options.dry_run = True

        mock_regctl_image_copy = self.patch_method(
            TagContainersCommand, "regctl_image_copy"
        )

        defaults = vars(self.options)
        parser = argparse.ArgumentParser()
        registry = bomtool_main.make_registry(
            [buildtool.container_commands], parser, defaults
        )
        bomtool_main.add_standard_parser_args(parser, defaults)
        factory = registry["tag_containers"]
        command = factory.make_command(options)
        command()

        self.assertEqual(0, mock_regctl_image_copy.call_count)

    def test_tag_containers(self):
        """Test tag_containers with dry_run disabled.
        Verify mocked regctl command is called for each tag permutation."""

        options = self.options

        options.spinnaker_version = "1.2.3"
        options.bom_path = os.path.join(
            os.path.dirname(__file__), "standard_test_bom.yml"
        )
        options.dry_run = False

        mock_regctl_image_copy = self.patch_method(
            TagContainersCommand, "regctl_image_copy"
        )

        defaults = vars(self.options)
        parser = argparse.ArgumentParser()
        registry = bomtool_main.make_registry(
            [buildtool.container_commands], parser, defaults
        )
        bomtool_main.add_standard_parser_args(parser, defaults)
        factory = registry["tag_containers"]
        command = factory.make_command(options)
        command()

        # BOM services gate & monitoring daemon should be tagged:
        # without "-unvalidated" and with "spinnaker-{version}", 4 variations
        # each service (2 services), 8 permutations.
        # BOM service "monitoring-third-party" should be ignored.
        calls = [
            call(
                f"{SPINNAKER_DOCKER_REGISTRY}/gate:7.8.9-20180102030405-unvalidated",
                f"{SPINNAKER_DOCKER_REGISTRY}/gate:7.8.9-20180102030405",
            ),
            call(
                f"{SPINNAKER_DOCKER_REGISTRY}/gate:7.8.9-20180102030405-unvalidated-ubuntu",
                f"{SPINNAKER_DOCKER_REGISTRY}/gate:7.8.9-20180102030405-ubuntu",
            ),
            call(
                f"{SPINNAKER_DOCKER_REGISTRY}/gate:7.8.9-20180102030405-unvalidated",
                f"{SPINNAKER_DOCKER_REGISTRY}/gate:spinnaker-1.2.3",
            ),
            call(
                f"{SPINNAKER_DOCKER_REGISTRY}/gate:7.8.9-20180102030405-unvalidated-ubuntu",
                f"{SPINNAKER_DOCKER_REGISTRY}/gate:spinnaker-1.2.3-ubuntu",
            ),
            call(
                f"{SPINNAKER_DOCKER_REGISTRY}/monitoring-daemon:7.8.9-20180908070605-unvalidated",
                f"{SPINNAKER_DOCKER_REGISTRY}/monitoring-daemon:7.8.9-20180908070605",
            ),
            call(
                f"{SPINNAKER_DOCKER_REGISTRY}/monitoring-daemon:7.8.9-20180908070605-unvalidated-ubuntu",
                f"{SPINNAKER_DOCKER_REGISTRY}/monitoring-daemon:7.8.9-20180908070605-ubuntu",
            ),
            call(
                f"{SPINNAKER_DOCKER_REGISTRY}/monitoring-daemon:7.8.9-20180908070605-unvalidated",
                f"{SPINNAKER_DOCKER_REGISTRY}/monitoring-daemon:spinnaker-1.2.3",
            ),
            call(
                f"{SPINNAKER_DOCKER_REGISTRY}/monitoring-daemon:7.8.9-20180908070605-unvalidated-ubuntu",
                f"{SPINNAKER_DOCKER_REGISTRY}/monitoring-daemon:spinnaker-1.2.3-ubuntu",
            ),
        ]

        mock_regctl_image_copy.assert_has_calls(calls)

        # Should only be eight permutations, no more (e.g: NOT monitoring-third-party)
        self.assertEqual(mock_regctl_image_copy.call_count, 8)


if __name__ == "__main__":
    init_runtime()
    unittest.main(verbosity=2)
