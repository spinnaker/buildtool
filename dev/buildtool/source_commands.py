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

"""Implements source commands for buildtool."""

import logging
import os
import shutil

from buildtool import (
    DEFAULT_BUILD_NUMBER,
    SPIN_REPOSITORY_NAMES,
    SPINNAKER_BOM_REPOSITORY_NAMES,
    SPINNAKER_HALYARD_REPOSITORY_NAME,
    SPINNAKER_LIBRARY_REPOSITORY_NAMES,
    SPINNAKER_PROCESS_REPOSITORY_NAMES,
    SPINNAKER_RUNNABLE_NON_CORE_REPOSITORY_NAMES,
    SPINNAKER_RUNNABLE_REPOSITORY_NAMES,
    BranchSourceCodeManager,
    GitRunner,
    RepositoryCommandFactory,
    RepositoryCommandProcessor,
    SemanticVersion,
    check_options_set,
    raise_and_log_error,
    ConfigError,
)


class FetchSourceCommand(RepositoryCommandProcessor):
    """Implements the fetch_source command."""

    def __init__(self, factory, options):
        """Implements CommandProcessor interface."""

        all_names = list(SPINNAKER_BOM_REPOSITORY_NAMES)
        all_names.append(SPINNAKER_HALYARD_REPOSITORY_NAME)
        all_names.extend(SPINNAKER_PROCESS_REPOSITORY_NAMES)
        super().__init__(factory, options, source_repository_names=all_names)

    def ensure_local_repository(self, repository):
        """Implements RepositoryCommandProcessor interface."""
        options = self.options
        if os.path.exists(repository.git_dir):
            if options.delete_existing:
                logging.warning("Deleting existing %s", repository.git_dir)
                shutil.rmtree(repository.git_dir)
            elif options.skip_existing:
                logging.debug("Skipping existing %s", repository.git_dir)
            else:
                raise_and_log_error(
                    ConfigError(
                        '"{dir}" already exists.'
                        ' Enable "skip_existing" or "delete_existing".'.format(
                            dir=repository.git_dir
                        )
                    )
                )
        super().ensure_local_repository(repository)

    def _do_repository(self, repository):
        """Implements RepositoryCommandProcessor interface."""
        pass


class FetchSourceCommandFactory(RepositoryCommandFactory):
    def __init__(self):
        super().__init__(
            "fetch_source",
            FetchSourceCommand,
            "Clone or refresh the local git repositories from the origin.",
            BranchSourceCodeManager,
        )

    def init_argparser(self, parser, defaults):
        super().init_argparser(parser, defaults)
        self.add_argument(
            parser,
            "build_number",
            defaults,
            DEFAULT_BUILD_NUMBER,
            help="The build number is used when generating artifacts.",
        )
        self.add_argument(
            parser,
            "delete_existing",
            defaults,
            False,
            type=bool,
            help="Force a new clone by removing existing directories if present.",
        )
        self.add_argument(
            parser,
            "skip_existing",
            defaults,
            False,
            type=bool,
            help="Ignore directories that are already present.",
        )


class ExtractSourceInfoCommand(RepositoryCommandProcessor):
    """Get the Git metadata for each repository, and associate a build number."""

    def _do_repository(self, repository):
        """Implements RepositoryCommandProcessor interface."""
        self.source_code_manager.refresh_source_info(
            repository, self.options.build_number
        )


class ExtractSourceInfoCommandFactory(RepositoryCommandFactory):
    """Associates the current build number with each repository."""

    def __init__(self):
        super().__init__(
            "extract_source_info",
            ExtractSourceInfoCommand,
            "Get the repository metadata and establish a build number.",
            BranchSourceCodeManager,
            source_repository_names=SPINNAKER_BOM_REPOSITORY_NAMES,
        )

    def init_argparser(self, parser, defaults):
        super().init_argparser(parser, defaults)
        self.add_argument(
            parser,
            "build_number",
            defaults,
            DEFAULT_BUILD_NUMBER,
            help="The build number is used when generating artifacts.",
        )


class NewReleaseBranchFactory(RepositoryCommandFactory):
    def __init__(self, **kwargs):
        all_names = list(SPINNAKER_RUNNABLE_REPOSITORY_NAMES)
        all_names.extend(SPINNAKER_RUNNABLE_NON_CORE_REPOSITORY_NAMES)
        all_names.extend(SPINNAKER_LIBRARY_REPOSITORY_NAMES)
        all_names.extend(SPIN_REPOSITORY_NAMES)
        super().__init__(
            "new_release_branch",
            NewReleaseBranchCommand,
            "Create a new release branch at the latest tag in each of the repos.",
            BranchSourceCodeManager,
            source_repository_names=all_names,
            **kwargs,
        )

    def init_argparser(self, parser, defaults):
        GitRunner.add_parser_args(parser, defaults)
        GitRunner.add_publishing_parser_args(parser, defaults)
        super().init_argparser(parser, defaults)
        self.add_argument(
            parser,
            "new_branch",
            defaults,
            None,
            help='The new release branch name should be "release-<num>.<num>.x"',
        )
        self.add_argument(
            parser,
            "delete_existing",
            defaults,
            False,
            type=bool,
            help="Delete existing release branch if present and create a new one.",
        )
        self.add_argument(
            parser,
            "skip_existing",
            defaults,
            False,
            type=bool,
            help="Skip repository if release branch already exists.",
        )


class NewReleaseBranchCommand(RepositoryCommandProcessor):
    def __init__(self, factory, options, **kwargs):
        super().__init__(factory, options, **kwargs)
        check_options_set(options, ["new_branch"])
        self.__git = GitRunner(options)

    def _do_repository(self, repository):
        git_dir = repository.git_dir
        branch = self.options.new_branch

        logging.debug('Checking for branch="%s" in "%s"', branch, git_dir)
        remote_branches = [
            line.strip()
            for line in self.__git.check_run(git_dir, "branch -r").split("\n")
        ]

        if "origin/" + branch in remote_branches:
            if self.options.skip_existing:
                logging.info(
                    'Branch "%s" already exists in "%s". Skipping.',
                    branch,
                    repository.origin,
                )
                return

            if self.options.delete_existing:
                logging.warning(
                    'Branch "%s" already exists in "%s". Deleting.',
                    branch,
                    repository.origin,
                )
                self.__git.delete_branch_on_origin(git_dir, branch)
            else:
                raise_and_log_error(
                    ConfigError(
                        f'Branch "{branch}" already exists in "{repository.name}"',
                        cause="branch_exists",
                    )
                )

        head_commit_id = self.__git.query_local_repository_commit_id(repository.git_dir)
        logging.debug("%s HEAD commit: %s", repository.name, head_commit_id)

        tag, _ = self.__git.find_newest_tag_and_common_commit_from_id(
            repository.git_dir, head_commit_id
        )

        logging.info(
            'Creating "%s" off "%s" at "%s" and pushing to "%s"',
            branch,
            self.options.git_branch,
            tag,
            repository.origin,
        )
        self.__git.create_branch(git_dir, branch, tag)
        self.__git.push_branch_to_origin(git_dir, branch)


class TagBranchCommand(RepositoryCommandProcessor):
    """Implements the tag_branch command."""

    def __init__(self, factory, options):
        """Implements CommandProcessor interface."""

        self.__git = GitRunner(options)

        all_names = list(SPINNAKER_RUNNABLE_REPOSITORY_NAMES)
        all_names.extend(SPINNAKER_RUNNABLE_NON_CORE_REPOSITORY_NAMES)
        all_names.extend(SPINNAKER_LIBRARY_REPOSITORY_NAMES)
        super().__init__(factory, options, source_repository_names=all_names)

    def _do_repository(self, repository):
        """Implements RepositoryCommandProcessor interface."""
        head_commit_id = self.__git.query_local_repository_commit_id(repository.git_dir)
        logging.debug(
            "%s %s branch HEAD commit: %s",
            repository.name,
            self.options.git_branch,
            head_commit_id,
        )

        (
            latest_tag,
            latest_tag_commit_id,
        ) = self.__git.find_newest_tag_and_common_commit_from_id(
            repository.git_dir, head_commit_id
        )
        if latest_tag_commit_id == head_commit_id:
            logging.info(
                "%s %s branch HEAD commit: %s already tagged at: %s. Skipping.",
                repository.name,
                self.options.git_branch,
                head_commit_id,
                latest_tag,
            )
            return

        logging.debug(
            "%s %s branch latest tag: %s - latest tag commit: %s",
            repository.name,
            self.options.git_branch,
            latest_tag,
            latest_tag_commit_id,
        )

        latest_tag_semver = SemanticVersion.make(latest_tag)

        if self.options.git_branch == "master":
            next_semver = latest_tag_semver.next(2)  # increment minor
        else:
            next_semver = latest_tag_semver.next(3)  # increment patch

        next_tag = next_semver.to_tag()

        logging.info(
            "%s %s branch latest tag: %s not at HEAD, generating next tag: %s",
            repository.name,
            self.options.git_branch,
            latest_tag,
            next_tag,
        )

        self.__git.tag_commit(repository.git_dir, next_tag, head_commit_id)
        self.__git.push_tag_to_origin(repository.git_dir, next_tag)


class TagBranchCommandFactory(RepositoryCommandFactory):
    def __init__(self):
        super().__init__(
            "tag_branch",
            TagBranchCommand,
            "Tag HEAD of branches if there are commits since the previous tag.",
            BranchSourceCodeManager,
        )

    def init_argparser(self, parser, defaults):
        super().init_argparser(parser, defaults)
        GitRunner.add_publishing_parser_args(parser, defaults)


def register_commands(registry, subparsers, defaults):
    ExtractSourceInfoCommandFactory().register(registry, subparsers, defaults)
    FetchSourceCommandFactory().register(registry, subparsers, defaults)
    NewReleaseBranchFactory().register(registry, subparsers, defaults)
    TagBranchCommandFactory().register(registry, subparsers, defaults)
