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

"""Implements spinnaker release commands for buildtool."""

import copy
import logging

from buildtool import (
    CommandProcessor,
    CommandFactory,
    GitRunner,
    check_options_set,
)

from buildtool.bom_commands import (
    BuildBomCommandFactory,
    PublishBomCommandFactory,
)
from buildtool.changelog_commands import BuildChangelogFactory, PublishChangelogFactory
from buildtool.container_commands import TagContainersFactory
from buildtool.source_commands import TagBranchCommandFactory, NewReleaseBranchFactory
from buildtool.versions_commands import (
    FetchVersionsFactory,
    PublishVersionsFactory,
    UpdateVersionsFactory,
)


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
            "minimum_halyard_version",
            defaults,
            None,
            help="The minimum halyard version required for this release.",
        )
        self.add_argument(
            parser,
            "latest_halyard_version",
            defaults,
            None,
            help="The latest halyard version available if changed.",
        )
        self.add_argument(
            parser,
            "dry_run",
            defaults,
            True,
            type=bool,
            help="Show proposed actions, don't actually do them. Default True.",
        )


class PublishSpinnakerCommand(CommandProcessor):
    """ "Implements the publish_spinnaker command."""

    # pylint: disable=too-few-public-methods

    def __init__(self, factory, options, **kwargs):
        super().__init__(factory, options, **kwargs)
        check_options_set(
            options,
            [
                "spinnaker_version",
                "minimum_halyard_version",
            ],
        )

        # Set some defaults that are defined in various other files
        # TODO: Consider making these CONSTANT or importing other factories?
        options.github_hostname = "github.com"

        # Default to suitable values for GitHub Actions running buildtool on git tag
        if options.github_owner is None:
            options.github_owner = "spinnaker"
        if "github_upstream_owner" not in options:
            options.github_upstream_owner = "spinnaker"
        if options.dry_run:
            logging.info(
                "Dry run selected, disabling push to git and artifact repositories"
            )
            options.git_never_push = True

        options.exclude_repositories = []
        options.only_repositories = None

    def __tag_branches(self, branch, options):
        """Tag branch with next version tag."""
        options.git_branch = branch
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
        logging.debug("Creating branches - options: %s", options)

        command = NewReleaseBranchFactory().make_command(options)
        command()

    def __build_bom(self, branch, version, options):
        """Build BOM."""
        # options.github_owner = "spinnaker"
        options.git_branch = branch
        options.build_number = version
        options.refresh_from_bom_path = "dev/buildtool/bom_base.yml"
        options.refresh_from_bom_version = None
        options.bom_dependencies_path = None
        options.bom_path = None
        options.exclude_repositories = "spinnaker-monitoring"
        logging.debug("Building BOM - options: %s", options)

        command = BuildBomCommandFactory().make_command(options)
        command()

    def __build_changelog(self, bom_path, previous_version, options):
        """Build Changelog."""
        options.bom_path = bom_path
        options.relative_to_bom_path = None
        options.relative_to_bom_version = previous_version
        options.include_changelog_details = False
        options.exclude_repositories = "spinnaker-monitoring"
        logging.debug("Building changelog - options: %s", options)

        command = BuildChangelogFactory().make_command(options)
        command()

    def __publish_bom(self, bom_path, options):
        """Publish BOM."""
        options.bom_path = bom_path
        logging.debug("Publishing BOM - options: %s", options)

        command = PublishBomCommandFactory().make_command(options)
        command()

    def __fetch_versions(self, options):
        """Fetch versions.yml."""
        logging.debug("Fetching versions.yml - options: %s", options)

        command = FetchVersionsFactory().make_command(options)
        command()

    def __update_versions(
        self,
        versions_yml_path,
        spinnaker_version,
        minimum_halyard_version,
        latest_halyard_version,
        options,
    ):
        """Update versions.yml."""
        logging.debug("Updating versions.yml - options: %s", options)
        options.versions_yml_path = versions_yml_path
        options.spinnaker_version = spinnaker_version
        options.miniumum_halyard_version = minimum_halyard_version
        options.latest_halyard_version = latest_halyard_version

        command = UpdateVersionsFactory().make_command(options)
        command()

    def __tag_containers(self, bom_path, spinnaker_version, options):
        """Tag containers."""
        logging.debug("Tagging containers - options: %s", options)
        options.bom_path = bom_path
        options.spinnaker_version = spinnaker_version

        command = TagContainersFactory().make_command(options)
        command()

    def __publish_versions(self, versions_yml_path, options):
        """Publish versions.yml."""
        options.versions_yml_path = versions_yml_path
        logging.debug("Publishing versions.yml - options: %s", options)

        command = PublishVersionsFactory().make_command(options)
        command()

    def __publish_changelog(self, changelog_path, version, options):
        """Publish Changelog."""
        options.changelog_path = changelog_path
        options.git_branch = "master"
        options.git_allow_publish_master_branch = False
        options.spinnaker_version = version
        logging.debug("Publishing changelog - options: %s", options)

        command = PublishChangelogFactory().make_command(options)
        command()

    def _do_command(self):
        """Implements CommandProcessor interface."""
        options = self.options
        logging.info("Publish Spinnaker - options: %s", options)

        # Setup Release defaults
        major_minor = get_major_minor_version(options.spinnaker_version)
        release_branch = f"release-{major_minor}.x"

        # Tag branches and create release-* branches as required
        # TODO: Reconcile bumpdeps. Until then tag and branch manually.
        # if is_new_minor_version(options.spinnaker_version):
        #     self.__tag_branches("master", copy.copy(options))
        #     self.__create_branches(release_branch, copy.copy(options))
        # else:
        #     self.__tag_branches(release_branch, copy.copy(options))

        # TODO: Validation each repository gradle.properties has correct dependency versions.

        # Build BOM
        self.__build_bom(release_branch, options.spinnaker_version, copy.copy(options))

        # Build Changelog
        previous_version = get_prior_version(options.spinnaker_version)
        logging.info(
            "spinnaker_version: %s - previous_version: %s",
            options.spinnaker_version,
            previous_version,
        )
        bom_path = f"output/build_bom/{release_branch}-{options.spinnaker_version}.yml"
        self.__build_changelog(bom_path, previous_version, copy.copy(options))
        changelog_path = "output/publish_spinnaker/changelog.md"

        # Build versions.yml
        self.__fetch_versions(copy.copy(options))
        self.__update_versions(
            "output/fetch_versions/versions.yml",
            options.spinnaker_version,
            options.minimum_halyard_version,
            options.latest_halyard_version,
            copy.copy(options),
        )

        # Publishing Actions
        self.__tag_containers(bom_path, options.spinnaker_version, copy.copy(options))

        self.__publish_changelog(
            changelog_path, options.spinnaker_version, copy.copy(options)
        )

        self.__publish_bom(bom_path, copy.copy(options))

        self.__publish_versions(
            "output/update_versions/versions.yml", copy.copy(options)
        )


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
