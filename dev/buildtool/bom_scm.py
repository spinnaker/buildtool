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

"""Source code manager that uses BOMs."""

import logging
import os
import yaml

from buildtool import (
    SPINNAKER_RUNNABLE_REPOSITORY_NAMES,
    HalRunner,
    SpinnakerSourceCodeManager,
    add_parser_argument,
    check_kwargs_empty,
    check_path_exists,
    raise_and_log_error,
    ConfigError,
    UnexpectedError,
)


SPINNAKER_BOM_REPOSITORY_NAMES = list(SPINNAKER_RUNNABLE_REPOSITORY_NAMES)
SPINNAKER_BOM_REPOSITORY_NAMES.extend(["spinnaker-monitoring"])


def check_bom_service(bom, service_name):
    services = bom.get("services", {})
    entry = services.get(service_name)
    if entry is None:
        raise_and_log_error(
            ConfigError(
                'BOM does not contain service "%s"' % service_name, cause="BadBom"
            ),
            'BOM missing "%s": %s' % (service_name, services.keys()),
        )
    return entry


class BomSourceCodeManager(SpinnakerSourceCodeManager):
    """Manages source code specified in a BOM."""

    @staticmethod
    def add_parser_args(parser, defaults):
        """Add standard parser arguments used by SourceCodeManager."""
        if hasattr(parser, "added_bom_scm"):
            return
        parser.added_bom_scm = True
        SpinnakerSourceCodeManager.add_parser_args(parser, defaults)
        HalRunner.add_parser_args(parser, defaults)
        add_parser_argument(
            parser,
            "bom_path",
            defaults,
            None,
            help="Use the sources specified in the BOM path.",
        )
        add_parser_argument(
            parser,
            "bom_version",
            defaults,
            None,
            help="Use the sources specified in the BOM version.",
        )

    @staticmethod
    def bom_from_path(path):
        """Load a BOM from a file."""
        logging.debug("Loading bom from %s", path)
        with open(path, "r") as f:
            bom_yaml_string = f.read()
        return yaml.safe_load(bom_yaml_string)

    @staticmethod
    def load_bom(options):
        """Helper function for initializing the BOM if one was specified."""
        bom_path = options.bom_path if hasattr(options, "bom_path") else None
        bom_version = options.bom_version if hasattr(options, "bom_version") else None

        have_bom_path = 1 if bom_path else 0
        have_bom_version = 1 if bom_version else 0
        if have_bom_path + have_bom_version != 1:
            raise_and_log_error(
                ConfigError('Expected exactly one of: "bom_path", or "bom_version"')
            )

        if bom_path:
            check_path_exists(bom_path, why="options.bom_path")
            return BomSourceCodeManager.bom_from_path(bom_path)

        if not bom_version:
            raise_and_log_error(UnexpectedError("Not reachable", cause="NotReachable"))

        logging.debug("Retrieving bom version %s", bom_version)
        return HalRunner(options).retrieve_bom_version(bom_version)

    @property
    def bom(self):
        """Returns the bom being used, if any."""
        return self.__bom

    def __init__(self, options, *pos_args, **kwargs):
        self.__bom = kwargs.pop("bom", None) or self.load_bom(options)
        super(BomSourceCodeManager, self).__init__(options, *pos_args, **kwargs)

    def get_repository_service_build_version(self, repository):
        if not self.__bom:
            raise_and_log_error(UnexpectedError("Missing bom", cause="NotReachable"))

        service_name = self.repository_name_to_service_name(repository.name)
        service_entry = self.__bom.get("services", {}).get(service_name, {})
        if not service_entry:
            raise_and_log_error(ConfigError("BOM missing service %s" % service_name))
        return service_entry["version"]

    def determine_bom_version(self):
        """Determine version of bound bom."""
        if self.__bom:
            return self.__bom["version"]
        if self.__options.bom_version:
            return self.__options.bom_version
        return None

    def determine_origin(self, name):
        service_name = self.repository_name_to_service_name(name)
        service = check_bom_service(self.__bom, service_name)
        if service.get("gitPrefix"):
            prefix = service["gitPrefix"]
        else:
            prefix = self.__bom["artifactSources"]["gitPrefix"]
        return prefix + "/" + name

    def ensure_git_path(self, repository, **kwargs):
        """Make sure repository path is consistent with BOM."""
        check_kwargs_empty(kwargs)
        service_name = self.repository_name_to_service_name(repository.name)
        if not service_name in self.__bom["services"].keys():
            raise_and_log_error(
                UnexpectedError('"%s" is not a BOM repo' % service_name)
            )

        git_dir = repository.git_dir
        have_git_dir = os.path.exists(git_dir)

        service = check_bom_service(self.__bom, service_name)
        commit_id = service["commit"]

        if not have_git_dir:
            self.git.clone_repository_to_path(repository, commit=commit_id)

    def determine_build_number(self, repository):
        service_name = self.repository_name_to_service_name(repository.name)
        if not service_name in self.__bom["services"].keys():
            raise_and_log_error(
                UnexpectedError('"%s" is not a BOM repo' % service_name)
            )

        service = check_bom_service(self.__bom, service_name)
        build_number = service["version"][service["version"].find("-") + 1 :]
        return build_number

    def determine_repository_version(self, repository):
        service_name = self.repository_name_to_service_name(repository.name)
        if not service_name in self.__bom["services"].keys():
            raise_and_log_error(
                UnexpectedError('"%s" is not a BOM repo' % service_name)
            )

        service = check_bom_service(self.__bom, service_name)
        version = service["version"][: service["version"].find("-")]
        return version

    def ensure_repository(self, repository):
        git_dir = repository.git_dir
        service_name = self.repository_name_to_service_name(repository.name)
        self.git.refresh_local_repository(git_dir, "origin")
        bom_commit = check_bom_service(self.__bom, service_name)["commit"]
        self.git.checkout(repository, bom_commit)
        return

    def check_repository_is_current(self, repository):
        git_dir = repository.git_dir
        service_name = self.repository_name_to_service_name(repository.name)
        have_commit = self.git.query_local_repository_commit_id(git_dir)
        bom_commit = check_bom_service(self.__bom, service_name)["commit"]
        if have_commit != bom_commit:
            raise_and_log_error(
                UnexpectedError(
                    '"%s" is at the wrong commit "%s"' % (git_dir, bom_commit)
                )
            )
        return True

    def determine_upstream_url(self, name):
        # Disable upstream on BOM urls since we wont be pushing back.
        return None

    def determine_source_repositories(self):
        """Implements SpinnakerSourceCodeManger interface."""
        bom = self.__bom
        git_prefix = bom["artifactSources"]["gitPrefix"]
        repositories = []
        for service_name, spec in bom["services"].items():
            if spec is None:
                logging.warning("Skipping %s because it was null", service_name)
                continue
            if service_name in ["monitoring-third-party", "defaultArtifact"]:
                continue
            repo_name = self.service_name_to_repository_name(service_name)

            prefix = spec["gitPrefix"] if "gitPrefix" in spec else git_prefix
            origin = "%s/%s" % (prefix, repo_name)
            repositories.append(self.make_repository_spec(repo_name, origin=origin))
        return repositories
