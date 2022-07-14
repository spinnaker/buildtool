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

"""Abstract CommandProcessor classes for commands on repositories and boms."""

import logging

# pylint: disable=relative-import
from buildtool import CommandProcessor, CommandFactory, maybe_log_exception


def _do_call_do_repository(repository, command):
    """Run the command's _do_repository on the given repository.

    This will track the invocation and its outcome.
    """
    # pylint: disable=protected-access
    logging.info("%s processing %s", command.name, repository.name)
    try:
        metric_labels = command.determine_metric_labels()
        metric_labels["repository"] = repository.name
        result = command.metrics.track_and_time_call(
            "RunRepositoryCommand",
            metric_labels,
            command.metrics.default_determine_outcome_labels,
            command._do_repository_wrapper,
            repository,
        )
        logging.info("%s finished %s", command.name, repository.name)
        return result
    except Exception as ex:
        maybe_log_exception(
            "{command} on repo={repo}".format(
                command=command.name, repo=repository.name
            ),
            ex,
        )
        raise


class RepositoryCommandProcessor(CommandProcessor):
    """And abstract command processor that run a command for each repository.

    Derived classes should override _do_repository() rather than _do_command().
    """

    @property
    def bom(self):
        """Return the bom, if one is bound."""
        return self.__scm.bom

    @property
    def git(self):
        return self.__scm.git

    @property
    def scm(self):
        return self.__scm

    @property
    def source_code_manager(self):
        return self.__scm

    @property
    def source_repositories(self):
        if self.__source_repositories is None:
            self.__source_repositories = self.filter_repositories(
                self.__scm.determine_source_repositories()
            )
        return self.__source_repositories

    def __init__(self, factory, options, **kwargs):
        source_repo_names = kwargs.pop("source_repository_names", None)
        max_threads = kwargs.pop("max_threads", 64)
        if options.one_at_a_time:
            logging.debug("Limiting %s to one thread.", factory.name)
            max_threads = 1

        super().__init__(factory, options, **kwargs)
        self.__scm = factory.make_scm(
            options, self.get_input_dir(), max_threads=max_threads
        )

        self.__source_repositories = None
        if source_repo_names:
            # filter needs the options, so this is after our super init call.
            if self.options.exclude_repositories:
                exclude_names = self.options.exclude_repositories.split(",")
            else:
                exclude_names = []
            if self.options.only_repositories:
                only_names = self.options.only_repositories.split(",")
            else:
                only_names = source_repo_names
            self.__source_repositories = self.filter_repositories(
                [
                    self.__scm.make_repository_spec(name)
                    for name in source_repo_names
                    if name in only_names and name not in exclude_names
                ]
            )

    def ensure_local_repository(self, repository):
        """Prepare the repository.git_dir."""
        self.__scm.ensure_local_repository(repository)

    def filter_repositories(self, source_repositories):
        """Filter a list of source_repositories using option constraints."""
        # pylint: disable=unused-argument
        if not (self.options.only_repositories or self.options.exclude_repositories):
            return source_repositories

        if self.options.only_repositories:
            restrict_filter = self.options.only_repositories.split(",")
        else:
            restrict_filter = []

        if self.options.exclude_repositories:
            exclude_filter = self.options.exclude_repositories.split(",")
        else:
            exclude_filter = []

        if not restrict_filter and exclude_filter:
            restrict_filter = [repo.name for repo in source_repositories]
        return [
            repository
            for repository in source_repositories
            if (
                (not restrict_filter or repository.name in restrict_filter)
                and repository.name not in exclude_filter
            )
        ]

    def _do_command(self):
        """Implements CommandProcessor interface.

        Derived classes should instead implement _do_repository to operate
        on individual repositories.

        They can also implement _do_preprocess or _do_postprocess to inject
        behavior before processing any repositories or after processing all them.
        """
        self._do_preprocess()
        result_dict = self.__scm.foreach_source_repository(
            self.source_repositories, _do_call_do_repository, self
        )
        return self._do_postprocess(result_dict)

    def _do_preprocess(self):
        """Prepares the command with any pre-requisites that can be factored out."""
        pass

    def _do_postprocess(self, result_dict):
        """Perform any post-process after all the repos have been processed.

        Args:
          result_dict: [dict] The result of the foreach_source_repository call.

        Returns:
          The final result for the command.
        """
        return result_dict

    def _do_repository_wrapper(self, repository):
        """Internal method doing some setup work before calling do repository.

        This is for instrumentation purposes.
        """
        if self._do_can_skip_repository(repository):
            self.metrics.inc_counter(
                "SkipRepositoryCommand",
                {"command": self.name, "repository": repository.name},
            )
            logging.debug("Skipping repository %s", repository.name)
            return None

        self.ensure_local_repository(repository)
        return self._do_repository(repository)

    def _do_can_skip_repository(self, repository):
        """Perform a check to see if the command can skip this repository.

        This is called prior to ensure_local_repository.
        """
        return False

    def _do_repository(self, repository):
        """This should be overriden to implement actual behavior."""
        raise NotImplementedError(
            f"{self.__class__.__name__}._do_repository({repository.name!r})"
        )


class RepositoryCommandFactory(CommandFactory):
    def __init__(
        self,
        name,
        factory_method,
        description,
        scm_factory,
        *factory_method_pos_args,
        **factory_method_kwargs
    ):
        super().__init__(
            name,
            factory_method,
            description,
            *factory_method_pos_args,
            **factory_method_kwargs
        )
        self.__scm_factory = scm_factory

    def make_scm(self, options, root_source_dir, **kwargs):
        """Create the SourceCodeManager for this command."""
        return self.__scm_factory(options, root_source_dir, **kwargs)

    def init_argparser(self, parser, defaults):
        """Hook for derived classes to override to add their specific arguments."""
        super().init_argparser(parser, defaults)
        self.__scm_factory.add_parser_args(parser, defaults)
        self.add_argument(
            parser,
            "only_repositories",
            defaults,
            None,
            help="Limit the command to the specified repositories."
            " This is a list of comma-separated repository names.",
        )
        self.add_argument(
            parser,
            "exclude_repositories",
            defaults,
            None,
            help="Do not apply the command to the specified repositories."
            " This is a list of comma-separated repository names."
            " This flag is intended for temporary use to bypass broken repos.",
        )
