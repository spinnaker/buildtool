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

"""Implements spinnaker support commands for buildtool."""

import argparse
import copy
import logging
import os
import subprocess
import yaml

from source_commands import TagBranchCommandFactory, NewReleaseBranchFactory

try:
    from urllib2 import urlopen, HTTPError
except ImportError:
    from urllib.request import urlopen
    from urllib.error import HTTPError

from buildtool import (
    CommandProcessor,
    CommandFactory,
    GitRunner,
    check_options_set,
    check_subprocess,
    raise_and_log_error,
    ConfigError,
)

from buildtool.changelog_commands import PublishChangelogFactory


class PublishSpinnakerFactory(CommandFactory):
    """ "Implements the publish_spinnaker command."""

    def __init__(self):
        super().__init__(
            "publish_spinnaker", PublishSpinnakerCommand, "Publish a spinnaker release"
        )

    def init_argparser(self, parser, defaults):
        super().init_argparser(parser, defaults)
        GitRunner.add_parser_args(parser, defaults)
        GitRunner.add_publishing_parser_args(parser, defaults)

        self.add_argument(
            parser,
            "spinnaker_version",
            defaults,
            None,
            help="The new version to publish.",
        )
        self.add_argument(
            parser,
            "min_halyard_version",
            defaults,
            None,
            help="The minimum halyard version required if changed.",
        )


class GetNextPatchParametersCommandFactory(CommandFactory):
    """ "Implements the get_next_patch_parameters command."""

    def __init__(self):
        super().__init__(
            "get_next_patch_parameters",
            GetNextPatchParametersCommand,
            "Get the parameters for the next patch release.",
        )

    def init_argparser(self, parser, defaults):
        super().init_argparser(parser, defaults)

        self.add_argument(
            parser,
            "major_minor_version",
            defaults,
            None,
            help="The major/minor version of Spinnaker we are patching (ex: 1.18).",
        )


class GetNextPatchParametersCommand(CommandProcessor):
    """ "Implements the get_next_patch_parameters command."""

    def __init__(self, factory, options, **kwargs):
        super().__init__(factory, options, **kwargs)
        check_options_set(
            options,
            [
                "major_minor_version",
            ],
        )

    def _do_command(self):
        """Implements CommandProcessor interface."""
        options = self.options

        current_version_details = self._find_matching_version(
            options.major_minor_version
        )
        result = {
            "bom_version": "release-{version}.x-latest-validated".format(
                version=options.major_minor_version
            ),
            "spinnaker_version": get_next_version(current_version_details["version"]),
            "min_halyard_version": current_version_details["minimumHalyardVersion"],
            "spinnaker_release_alias": current_version_details["alias"],
            "changelog_gist_url": current_version_details["changelog"],
        }
        self._output_as_property_file(result)

    def _output_as_property_file(self, result):
        for key, value in result.items():
            print(f"{key.upper()}={value}")

    def _find_matching_version(self, major_minor_version):
        versions = self._get_versions()
        version_filter = (
            lambda v: get_major_minor_version(v.get("version")) == major_minor_version
        )
        try:
            return next(v for v in versions if version_filter(v))
        except StopIteration:
            raise_and_log_error(
                ConfigError(
                    "There are no active Spinnaker versions for version {branch}.".format(
                        branch=major_minor_version
                    ),
                    cause="IncorrectVersion",
                )
            )

    def _get_versions(self):
        version_data = check_subprocess(
            "gsutil cat gs://halconfig/versions.yml", stderr=subprocess.PIPE
        )
        versions = yaml.safe_load(version_data).get("versions")
        return versions


class PublishSpinnakerCommand(CommandProcessor):
    """ "Implements the publish_spinnaker command."""

    # pylint: disable=too-few-public-methods

    def __init__(self, factory, options, **kwargs):
        super().__init__(factory, options, **kwargs)
        check_options_set(
            options,
            [
                "spinnaker_version",
                "github_owner",
                # "min_halyard_version", # we will use latest in versions.yml
            ],
        )

        # Set some defaults that are defined in various other files
        # TODO: Consider making these CONSTANT or importing other factories?
        options.github_hostname = "github.com"
        options.github_upstream_owner = "spinnaker"
        options.exclude_repositories = []
        options.only_repositories = None

        # major, minor, _ = self.options.spinnaker_version.split(".")
        # self.__branch = f"release-{major}.{minor}.x"

        # options_copy = copy.copy(options)
        # self.__git = GitRunner(options)

        # if options.only_repositories:
        #     self.__only_repositories = options.only_repositories.split(",")
        # else:
        #     self.__only_repositories = []

        # options_copy.git_branch = self.__branch
        # self.__branch_scm = BranchSourceCodeManager(options_copy, self.get_input_dir())

    def __tag_branches(self, branch, options):
        """Tag branch with next version tag."""
        options.git_branch = branch
        options.git_never_push = True
        logging.debug("Tagging branches - options: %s", options)

        options.only_repositories = "kork"
        tag_branch_kork_command = TagBranchCommandFactory().make_command(options)
        tag_branch_kork_command()

        # TODO: wait for bumpdeps
        options.only_repositories = "fiat"
        tag_branch_fiat_command = TagBranchCommandFactory().make_command(options)
        tag_branch_fiat_command()

        # TODO: wait for bumpdeps
        options.only_repositories = "orca"
        tag_branch_orca_command = TagBranchCommandFactory().make_command(options)
        tag_branch_orca_command()

        # TODO: wait for bumpdeps
        options.only_repositories = None
        options.exclude_repositories = "kork,fiat,orca"
        tag_branch_rest_command = TagBranchCommandFactory().make_command(options)
        tag_branch_rest_command()

    def __create_branches(self, branch, options):
        """Create release-* branch."""
        options.git_branch = "master"
        options.new_branch = branch
        options.git_never_push = True
        logging.debug("Creating branches - options: %s", options)

        create_branches_command = NewReleaseBranchFactory().make_command(options)
        create_branches_command()

    def _do_command(self):
        """Implements CommandProcessor interface."""
        options = self.options
        logging.info("Publish Spinnaker - options: %s", options)

        # Setup Release defaults
        major_minor = get_major_minor_version(options.spinnaker_version)
        release_branch = f"release-{major_minor}.x"

        # Tag branches and create release-* branches as required
        tag_options = copy.copy(options)
        if is_new_minor_version(options.spinnaker_version):
            self.__tag_branches("master", tag_options)
            self.__create_branches(release_branch, copy.copy(options))
        else:
            self.__tag_branches(release_branch, tag_options)

        # TODO: Validation each repository gradle.properties has correct dependency versions.

        # determine previous version for use in bom, changelog, etc, eg: 1.27.1 -> 1.27.0. 1.28.0 -> 1.27.0
        previous_version = get_prior_version(options.spinnaker_version)
        logging.info(
            "spinnaker_version: %s - previous_version: %s",
            options.spinnaker_version,
            previous_version,
        )

        # Build BOM

        # Build Changelog

        # Build versions.yml

        # Publish BOM, Changelog, versions.yml

        # options_copy.git_branch = "master"  # push to master in spinnaker.io
        # publish_changelog_command = PublishChangelogFactory().make_command(options_copy)

        # bom = self.__hal.retrieve_bom_version(self.options.bom_version)
        # bom["version"] = spinnaker_version
        # bom_path = os.path.join(self.get_output_dir(), spinnaker_version + ".yml")
        # write_to_path(yaml.safe_dump(bom, default_flow_style=False), bom_path)
        # self.__hal.publish_bom_path(bom_path)
        # self.push_branches_and_tags(bom)

        # publish_changelog_command()


def is_new_minor_version(version):
    _, _, patch = version.split(".")
    patch = int(patch)
    if patch == 0:
        return True
    return False


def get_prior_version(version):
    major, minor, patch = version.split(".")
    patch = int(patch)
    minor = int(minor)
    if patch == 0:
        return f"{major}.{str(minor - 1)}.{patch}"
    return f"{major}.{minor}.{str(patch - 1)}"


def get_next_version(version):
    major, minor, patch = version.split(".")
    patch = int(patch)
    return f"{major}.{minor}.{str(patch + 1)}"


def get_major_minor_version(version):
    major, minor, _ = version.split(".")
    return f"{major}.{minor}"


def register_commands(registry, subparsers, defaults):
    PublishSpinnakerFactory().register(registry, subparsers, defaults)
    GetNextPatchParametersCommandFactory().register(registry, subparsers, defaults)
