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

"""Implements bom commands for buildtool."""

import datetime
import logging
import os
import yaml

from buildtool import (
    DEFAULT_BUILD_NUMBER,
    SPINNAKER_BOM_REPOSITORY_NAMES,
    SPINNAKER_DEBIAN_REPOSITORY,
    SPINNAKER_DOCKER_REGISTRY,
    SPINNAKER_GOOGLE_IMAGE_PROJECT,
    SPINNAKER_HALYARD_GCS_BUCKET_NAME,
    BomSourceCodeManager,
    BranchSourceCodeManager,
    CommandProcessor,
    CommandFactory,
    RepositoryCommandFactory,
    RepositoryCommandProcessor,
    GitRunner,
    check_options_set,
    check_path_exists,
    check_subprocess,
    raise_and_log_error,
    write_to_path,
    ConfigError,
)


def _determine_bom_path(command_processor):
    if command_processor.options.bom_path:
        return command_processor.options.bom_path

    options = command_processor.options
    filename = "{branch}-{buildnum}.yml".format(
        branch=options.git_branch or "NOBRANCH", buildnum=options.build_number
    )
    return os.path.join(command_processor.get_output_dir(command="build_bom"), filename)


def now():
    """Hook for easier mocking."""
    return datetime.datetime.utcnow()


class BomBuilder:
    """Helper class for BuildBomCommand that constructs the bom specification."""

    @staticmethod
    def new_from_bom(options, scm, metrics, bom):
        return BomBuilder(options, scm, metrics, base_bom=bom)

    @property
    def base_bom(self):
        return self.__base_bom

    def __init__(self, options, scm, metrics, base_bom=None):
        """Construct new builder.

        Args:
          base_bom[dict]: If defined, this is a bom to start with.
                          It is intended to support a "refresh" use case where
                          only a subset of entires are updated within it.
        """
        self.__options = options
        self.__scm = scm
        self.__metrics = metrics
        self.__services = {}
        self.__repositories = {}
        self.__base_bom = base_bom or {}
        if not base_bom and not options.bom_dependencies_path:
            self.__bom_dependencies_path = os.path.join(
                os.path.dirname(__file__), "bom_dependencies.yml"
            )
        else:
            self.__bom_dependencies_path = options.bom_dependencies_path

        if self.__bom_dependencies_path:
            check_path_exists(self.__bom_dependencies_path, "bom_dependencies_path")

    def to_git_url_prefix(self, url):
        """Determine url up to the terminal path component."""
        if url.startswith("git@"):
            parts = GitRunner.normalize_repo_url(url)
            url = GitRunner.make_https_url(*parts)

        # We're assuming no query parameter/fragment since these are git URLs.
        # otherwise we need to parse the url and extract the path
        return url[: url.rfind("/")]

    def add_repository(self, repository, source_info):
        """Helper function for determining the repository's BOM entry."""
        version_info = {
            "commit": source_info.summary.commit_id,
            "version": source_info.to_build_version(),
        }

        service_name = self.__scm.repository_name_to_service_name(repository.name)
        self.__services[service_name] = version_info
        self.__repositories[service_name] = repository
        if service_name == "monitoring-daemon":
            # Dont use the same actual object because having the repeated
            # value reference causes the generated yaml to be invalid.
            self.__services["monitoring-third-party"] = dict(version_info)
            self.__repositories["monitoring-third-party"] = repository

    def determine_most_common_prefix(self):
        """Determine which of repositories url's is most commonly used."""
        prefix_count = {}
        for repository in self.__repositories.values():
            url_prefix = self.to_git_url_prefix(repository.origin)
            prefix_count[url_prefix] = prefix_count.get(url_prefix, 0) + 1
        default_prefix = None
        max_count = 0
        for prefix, count in prefix_count.items():
            if count > max_count:
                default_prefix, max_count = prefix, count
        return default_prefix

    def build(self):
        options = self.__options

        if self.__bom_dependencies_path:
            logging.debug(
                "Loading bom dependencies from %s", self.__bom_dependencies_path
            )
            with open(self.__bom_dependencies_path, encoding="UTF-8") as stream:
                dependencies = yaml.safe_load(stream.read())
                logging.debug("Loaded %s", dependencies)
        else:
            dependencies = None
        if not dependencies:
            dependencies = self.__base_bom.get("dependencies")

        if not dependencies:
            raise_and_log_error(ConfigError("No BOM dependencies found"))

        base_sources = self.__base_bom.get("artifactSources", {})
        default_source_prefix = (
            base_sources.get("gitPrefix", None) or self.determine_most_common_prefix()
        )
        for name, version_info in self.__services.items():
            repository = self.__repositories[name]
            origin = repository.origin
            source_prefix = self.to_git_url_prefix(origin)
            if source_prefix != default_source_prefix:
                version_info["gitPrefix"] = source_prefix

        artifact_sources = {
            "gitPrefix": default_source_prefix,
            "debianRepository": SPINNAKER_DEBIAN_REPOSITORY,
            "dockerRegistry": SPINNAKER_DOCKER_REGISTRY,
            "googleImageProject": SPINNAKER_GOOGLE_IMAGE_PROJECT,
        }

        services = dict(self.__base_bom.get("services", {}))
        changed = False

        def to_semver(build_version):
            index = build_version.find("-")
            return build_version[:index] if index >= 0 else build_version

        for name, info in self.__services.items():
            labels = {"repostiory": name, "branch": options.git_branch, "updated": True}
            if info["commit"] == services.get(name, {}).get("commit", None):
                if to_semver(info["version"]) == to_semver(
                    services.get(name, {}).get("version", "")
                ):
                    logging.debug(
                        "%s commit hasnt changed -- keeping existing %s", name, info
                    )
                    labels["updated"] = False
                    labels["reason"] = "same commit"
                    self.__metrics.inc_counter("UpdateBomEntry", labels)
                    continue

                # An earlier branch was patched since our base bom.
                labels["reason"] = "different version"
                logging.debug(
                    "%s version changed to %s even though commit has not",
                    name,
                    info,
                )
            else:
                labels["reason"] = "different commit"
            self.__metrics.inc_counter("UpdateBomEntry", labels)

            changed = True
            services[name] = info

        if (
            self.__base_bom.get("artifactSources") != artifact_sources
            or self.__base_bom.get("dependencies") != dependencies
        ):
            changed = True

        if not changed:
            return self.__base_bom

        return {
            "artifactSources": artifact_sources,
            "dependencies": dependencies,
            "services": services,
            "version": options.build_number,
            "timestamp": f"{now():%Y-%m-%d %H:%M:%S}",
        }


class BuildBomCommand(RepositoryCommandProcessor):
    """Implements build_bom."""

    def __init__(self, factory, options, *pos_args, **kwargs):
        super().__init__(factory, options, *pos_args, **kwargs)

        if options.refresh_from_bom_path and options.refresh_from_bom_version:
            raise_and_log_error(
                ConfigError(
                    'Cannot specify both --refresh_from_bom_path="{}"'
                    ' and --refresh_from_bom_version="{}"'.format(
                        options.refresh_from_bom_path, options.refresh_from_bom_version
                    )
                )
            )
        if options.refresh_from_bom_path:
            logging.debug(
                'Using base bom from path "%s"', options.refresh_from_bom_path
            )
            check_path_exists(options.refresh_from_bom_path, "refresh_from_bom_path")
            with open(options.refresh_from_bom_path, encoding="utf-8") as stream:
                base_bom = yaml.safe_load(stream.read())
        elif options.refresh_from_bom_version:
            logging.debug(
                'Using base bom version "%s"', options.refresh_from_bom_version
            )
            base_bom = BomSourceCodeManager.bom_from_gcs_bucket(
                options.refresh_from_bom_version
            )
        else:
            base_bom = None
        if base_bom:
            logging.info(
                'Creating new bom based on version "%s"',
                base_bom.get("version", "UNKNOWN"),
            )
        self.__builder = BomBuilder(
            self.options, self.scm, self.metrics, base_bom=base_bom
        )

    def _do_repository(self, repository):
        source_info = self.scm.refresh_source_info(
            repository, self.options.build_number
        )
        self.__builder.add_repository(repository, source_info)

    def _do_postprocess(self, _):
        """Construct BOM and write it to the configured path."""
        bom = self.__builder.build()
        if bom == self.__builder.base_bom:
            logging.info(
                "Bom has not changed from version %s @ %s",
                bom["version"],
                bom["timestamp"],
            )

        bom_text = yaml.safe_dump(bom, default_flow_style=False)

        path = _determine_bom_path(self)
        write_to_path(bom_text, path)
        logging.info("Wrote bom to %s", path)


class BuildBomCommandFactory(RepositoryCommandFactory):
    """Builds BOM - Bill of Materials"""

    def __init__(self, **kwargs):
        super().__init__(
            "build_bom",
            BuildBomCommand,
            "Build a BOM file.",
            BranchSourceCodeManager,
            source_repository_names=SPINNAKER_BOM_REPOSITORY_NAMES,
            **kwargs,
        )

    def init_argparser(self, parser, defaults):
        super().init_argparser(parser, defaults)

        self.add_argument(
            parser,
            "publish_gce_image_project",
            defaults,
            None,
            help="Project to publish images to.",
        )

        self.add_argument(
            parser,
            "build_number",
            defaults,
            DEFAULT_BUILD_NUMBER,
            help="The build number for this specific bom.",
        )
        self.add_argument(
            parser,
            "bom_path",
            defaults,
            None,
            help="The path to the local BOM file copy to write out.",
        )
        self.add_argument(
            parser,
            "bom_dependencies_path",
            defaults,
            None,
            help="The path to YAML file specifying the BOM dependencies section"
            " if overriding.",
        )
        self.add_argument(
            parser,
            "refresh_from_bom_path",
            defaults,
            None,
            help="If specified then use the existing bom_path as a prototype"
            " to refresh. Use with --only_repositories to create a new BOM."
            " using only the new versions and build numbers for select repos"
            " while keeping the existing versions and build numbers for"
            " others.",
        )
        self.add_argument(
            parser,
            "refresh_from_bom_version",
            defaults,
            None,
            help="Similar to refresh_from_bom_path but using a version obtained."
            " from GCS Bucket.",
        )
        self.add_argument(
            parser,
            "git_fallback_branch",
            defaults,
            None,
            help="The branch to pull for the BOM if --git_branch isnt found."
            " This is intended only for speculative development where"
            " some repositories are being modified and the remaing are"
            " to come from a release branch.",
        )


class PublishBomCommand(CommandProcessor):
    """ "Implements the publish_bom command."""

    def __init__(self, factory, options, **kwargs):
        super().__init__(factory, options, **kwargs)
        check_options_set(
            options,
            [
                "bom_path",
            ],
        )

    def gcs_upload(self, file, url):
        """Upload file to GCS Bucket"""
        # https://github.com/google-github-actions/auth/blob/main/README.md#other-inputs
        # describes GOOGLE_GHA_CREDS_PATH
        if "GOOGLE_GHA_CREDS_PATH" in os.environ:
            creds_path=os.environ["GOOGLE_GHA_CREDS_PATH"]
            result = check_subprocess(f"gcloud auth activate-service-account --key-file={creds_path}")
            logging.info("gcloud auth result: %s", result)

        result = check_subprocess(f"gsutil cp {file} {url}")
        logging.info("Published BOM: %s", result)

    def _do_command(self):
        """Implements CommandProcessor interface."""
        options = self.options
        logging.info("options: %s", options)

        check_path_exists(options.bom_path, "bom_path")
        bom_data = BomSourceCodeManager.bom_from_path(options.bom_path)
        version = bom_data.get("version")

        if not version:
            raise_and_log_error(ConfigError("BOM malformed, missing version key"))

        bom_url = f"gs://{SPINNAKER_HALYARD_GCS_BUCKET_NAME}/bom/{version}.yml"

        if options.dry_run:
            logging.info(
                "Dry run selected, not publishing %s BOM to: %s", version, bom_url
            )
        else:
            logging.info("Publishing %s BOM to: %s", version, bom_url)
            self.gcs_upload(options.bom_path, bom_url)


class PublishBomCommandFactory(CommandFactory):
    """Publishes BOM to GCS Bucket"""

    def __init__(self, **kwargs):
        super().__init__(
            "publish_bom",
            PublishBomCommand,
            "Publish a BOM file.",
            **kwargs,
        )

    def init_argparser(self, parser, defaults):
        super().init_argparser(parser, defaults)

        self.add_argument(
            parser,
            "bom_path",
            defaults,
            None,
            help="The path to the local BOM file to publish.",
        )

        self.add_argument(
            parser,
            "dry_run",
            defaults,
            True,
            type=bool,
            help="Show proposed actions, don't actually do them. Default True.",
        )


def register_commands(registry, subparsers, defaults):
    BuildBomCommandFactory().register(registry, subparsers, defaults)
    PublishBomCommandFactory().register(registry, subparsers, defaults)
