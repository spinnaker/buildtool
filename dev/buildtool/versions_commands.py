# Copyright 2022 Salesforce.com Inc. All Rights Reserved.
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

"""Implements versions commands for buildtool."""

import copy
import logging
import os
import time
import yaml

from schema import Optional, Regex, Schema, SchemaError

from buildtool import (
    SPINNAKER_HALYARD_GCS_BUCKET_NAME,
    SPINNAKER_CHANGELOG_BASE_URL,
    CommandFactory,
    CommandProcessor,
    check_options_set,
    check_path_exists,
    check_subprocess,
    raise_and_log_error,
    write_to_path,
)

# Three stable versions are maintained at a time. https://spinnaker.io/docs/releases/versions/
MAXIMUM_SPINNAKER_RELEASE_COUNT = 3

# https://semver.org/#is-there-a-suggested-regular-expression-regex-to-check-a-semver-string
SEMVER_REGEX = r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?(?:\+(?P<buildmetadata>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"

# https://github.com/spinnaker/halyard/blob/d6b5ec9b98d4903824c18a603884dde0c4f4165a/halyard-core/src/main/java/com/netflix/spinnaker/halyard/core/registry/v1/Versions.java#L33
VERSIONS_YML_SCHEMA = Schema(
    {
        Optional("illegalVersions"): [{"reason": str, "version": Regex(SEMVER_REGEX)}],
        "latestHalyard": Regex(SEMVER_REGEX),
        "latestSpinnaker": Regex(SEMVER_REGEX),
        "versions": [
            {
                "alias": str,
                "changelog": str,
                "lastUpdate": int,
                "minimumHalyardVersion": str,
                "version": Regex(SEMVER_REGEX),
            }
        ],
    }
)


def now():
    """Hook for easier mocking."""
    return time.time_ns() // 1_000_000


class VersionsBuilder:
    """Helper class for UpdateVersionsCommand that constructs the versions.yml specification."""

    @staticmethod
    def new_from_versions(versions):
        """Create a new VersionsBuilder with the supplied versions.yml as a dict."""
        return VersionsBuilder(base_versions=versions)

    @property
    def base_versions(self):
        return self.__base_versions_dict

    def __init__(self, base_versions=None):
        """Construct new builder.

        Args:
          base_versions[dict]: If defined, this is a versions dict to start with.
                          It is intended to support an "update" use case where
                          only a subset of entries are updated within it.
        """
        self.__base_versions_dict = base_versions or {
            "latestHalyard": "0.0.0",
            "latestSpinnaker": "0.0.0",
            "versions": [],
        }

    @staticmethod
    def validate_versions_yml(versions_dict):
        """Validate versions.yml adheres to schema."""
        try:
            VERSIONS_YML_SCHEMA.validate(versions_dict)
            logging.debug("Validated versions.yml schema")
        except SchemaError as se:
            raise se

    @staticmethod
    def build_single_release_dict(spinnaker_version, minimum_halyard_version):
        """Build a single release object for inclusion in available Spinnaker
        versions list."""
        current_epoch_ms = now()
        release = {
            "alias": f"v{spinnaker_version}",
            "changelog": f"{SPINNAKER_CHANGELOG_BASE_URL}/{spinnaker_version}-changelog/",
            "lastUpdate": current_epoch_ms,
            "minimumHalyardVersion": f"{minimum_halyard_version}",
            "version": spinnaker_version,
        }
        return release

    @staticmethod
    def get_major_minor_patch_version(version):
        """Split a M.m.p version string into an list of [major, minor, patch]."""
        major, minor, patch = version.split(".")
        return [major, minor, patch]

    def add_or_replace_release(self, new_release, versions):
        """Add new minor release versions to version list. If it's a new patch
        release version then find the previous patch version in the list and
        replace it. For example 1.28.0 will be added. 1.27.1 will replace 1.27.0."""

        new_release_semver = new_release["version"]

        patched_existing_version = False

        # loop over list in versions.yml["versions"]
        for idx, existing_release in enumerate(versions):

            existing_release_semver = existing_release["version"]

            major1, minor1, patch1 = self.get_major_minor_patch_version(
                new_release_semver
            )
            major2, minor2, patch2 = self.get_major_minor_patch_version(
                existing_release_semver
            )

            # check if matching minor, eg: new 1.27.1 and current version_block is 1.27.x
            if (major1 == major2) and (minor1 == minor2):
                if patch1 == patch2:
                    raise_and_log_error(
                        ValueError(
                            f"Spinnaker version already exists in versions.yml: {new_release_semver}"
                        )
                    )

                elif patch1 < patch2:
                    # new version older than previous release.
                    # e.g: spinnaker_version = 1.27.0 but versions.yml has 1.27.1
                    raise_and_log_error(
                        ValueError(
                            f"Spinnaker version {new_release_semver} superceded by newer patch version: {existing_release_semver}"
                        )
                    )

                else:
                    logging.info(
                        "Replacing version %s with new version %s",
                        existing_release_semver,
                        new_release_semver,
                    )
                    versions[idx] = new_release
                    patched_existing_version = True

        if not patched_existing_version:
            versions.append(new_release)

        return versions

    def build(
        self,
        new_spinnaker_version,
        minimum_halyard_version,
        latest_halyard_version=None,
    ):
        """Build versions.yml."""

        new_release = self.build_single_release_dict(
            new_spinnaker_version, minimum_halyard_version
        )

        logging.debug("Updating versions.yml with: %s", new_release)

        updated_versions = self.add_or_replace_release(
            new_release, self.__base_versions_dict["versions"]
        )

        sorted_versions = sorted(
            updated_versions, key=lambda d: d["version"], reverse=True
        )

        trimmed_versions = sorted_versions[:MAXIMUM_SPINNAKER_RELEASE_COUNT]

        final_versions = self.__base_versions_dict
        final_versions["versions"] = trimmed_versions
        final_versions["latestSpinnaker"] = final_versions["versions"][0]["version"]

        if latest_halyard_version:
            final_versions["latestHalyard"] = latest_halyard_version

        self.validate_versions_yml(final_versions)

        return final_versions


class FetchVersionsFactory(CommandFactory):
    """Fetches versions.yml file."""

    # pylint: disable=too-few-public-methods

    def __init__(self, **kwargs):
        super().__init__(
            "fetch_versions",
            FetchVersionsCommand,
            "Fetch Halyard's versions.yml file.",
            **kwargs,
        )


class FetchVersionsCommand(CommandProcessor):
    """Implements the fetch_versions command."""

    # pylint: disable=too-few-public-methods

    def __init__(self, factory, options, **kwargs):
        options_copy = copy.copy(options)

        super().__init__(factory, options_copy, **kwargs)

    def _do_command(self):
        """Implements CommandProcessor interface."""

        logging.info(
            "Fetching Spinnaker versions from gs://%s/versions.yml",
            SPINNAKER_HALYARD_GCS_BUCKET_NAME,
        )

        version_data = check_subprocess(
            f"gsutil cat gs://{SPINNAKER_HALYARD_GCS_BUCKET_NAME}/versions.yml"
        )
        versions_yaml = yaml.safe_load(version_data)

        versions_text = yaml.safe_dump(versions_yaml, default_flow_style=False)
        path = "output/fetch_versions/versions.yml"
        write_to_path(versions_text, path)
        logging.info("Wrote Spinnaker versions to %s", path)


class UpdateVersionsFactory(CommandFactory):
    """Updates versions.yml file."""

    # pylint: disable=too-few-public-methods

    def __init__(self, **kwargs):
        super().__init__(
            "update_versions",
            UpdateVersionsCommand,
            "Update Halyard's versions.yml and write it out to a file.",
            **kwargs,
        )

    def init_argparser(self, parser, defaults):
        """Adds command-specific arguments."""
        super().init_argparser(parser, defaults)

        self.add_argument(
            parser,
            "versions_yml_path",
            defaults,
            None,
            help="The path to the local versions.yml file to update.",
        )

        self.add_argument(
            parser,
            "latest_halyard_version",
            defaults,
            None,
            help="The latest Halyard version, not necessarily the latest required by Spinnaker.",
        )

        self.add_argument(
            parser,
            "spinnaker_version",
            defaults,
            None,
            help="The new version to add.",
        )

        self.add_argument(
            parser,
            "minimum_halyard_version",
            defaults,
            None,
            help="The minimum Halyard version required for spinnaker_version.",
        )


class UpdateVersionsCommand(CommandProcessor):
    """Implements the update_versions command."""

    # pylint: disable=too-few-public-methods

    def __init__(self, factory, options, **kwargs):
        check_options_set(
            options,
            [
                "versions_yml_path",
                # "latest_halyard_version" # optional
                "spinnaker_version",
                "minimum_halyard_version",
            ],
        )

        super().__init__(factory, options, **kwargs)

    def __load_versions_from_path(self, path):
        """Load versions.yml from a file."""
        logging.debug("Loading versions.yml from %s", path)
        with open(path, encoding="utf-8") as f:
            versions_yaml_string = f.read()
        versions_dict = yaml.safe_load(versions_yaml_string)

        return versions_dict

    def _do_command(self):
        """Implements CommandProcessor interface."""
        options = self.options

        check_path_exists(options.versions_yml_path, why="options.versions_yml_path")
        versions_dict = self.__load_versions_from_path(options.versions_yml_path)

        if options.latest_halyard_version is None:
            logging.debug(
                "latest_halyard_version not set, using existing value from versions.yml: %s",
                versions_dict["latestHalyard"],
            )
            options.latest_halyard_version = versions_dict["latestHalyard"]

        builder = VersionsBuilder.new_from_versions(versions_dict)

        updated_versions = builder.build(
            options.spinnaker_version,
            options.minimum_halyard_version,
            options.latest_halyard_version,
        )

        versions_text = yaml.safe_dump(updated_versions, default_flow_style=False)
        path = "output/update_versions/versions.yml"
        write_to_path(versions_text, path)
        logging.info("Wrote Spinnaker versions to %s", path)


class PublishVersionsFactory(CommandFactory):
    """Publish versions.yml file."""

    # pylint: disable=too-few-public-methods

    def __init__(self, **kwargs):
        super().__init__(
            "publish_versions",
            PublishVersionsCommand,
            "Publish Halyard's versions.yml.",
            **kwargs,
        )

    def init_argparser(self, parser, defaults):
        super().init_argparser(parser, defaults)

        self.add_argument(
            parser,
            "versions_yml_path",
            defaults,
            None,
            help="The path to the local versions.yml file to publish.",
        )

        self.add_argument(
            parser,
            "dry_run",
            defaults,
            True,
            type=bool,
            help="Show proposed actions, don't actually do them. Default True.",
        )


class PublishVersionsCommand(CommandProcessor):
    """Implements publish_versions command."""

    # pylint: disable=too-few-public-methods

    def __init__(self, factory, options, **kwargs):
        check_options_set(options, ["versions_yml_path"])
        check_path_exists(options.versions_yml_path, why="versions_yml_path")

        options_copy = copy.copy(options)
        super().__init__(factory, options_copy, **kwargs)

    def gcs_upload(self, file, url):
        """Upload file to GCS Bucket"""
        # https://github.com/google-github-actions/auth/blob/main/README.md#other-inputs
        # describes GOOGLE_GHA_CREDS_PATH
        if "GOOGLE_GHA_CREDS_PATH" in os.environ:
            creds_path=os.environ["GOOGLE_GHA_CREDS_PATH"]
            result = check_subprocess(f"gcloud auth activate-service-account --key-file={creds_path}")
            logging.info("gcloud auth result: %s", result)

        result = check_subprocess(f"gsutil cp {file} {url}")
        logging.info("Published versions.yml: %s", result)

    def _do_command(self):
        """Implements CommandProcessor interface."""
        options = self.options

        versions_url = f"gs://{SPINNAKER_HALYARD_GCS_BUCKET_NAME}/versions.yml"

        if options.dry_run:
            logging.info(
                "Dry run selected, not publishing %s to: %s",
                options.versions_yml_path,
                versions_url,
            )
        else:
            logging.info(
                "Publishing %s to: %s", options.versions_yml_path, versions_url
            )
            self.gcs_upload(options.versions_yml_path, versions_url)


def register_commands(registry, subparsers, defaults):
    """Registers all the commands for this module."""
    FetchVersionsFactory().register(registry, subparsers, defaults)
    UpdateVersionsFactory().register(registry, subparsers, defaults)
    PublishVersionsFactory().register(registry, subparsers, defaults)
