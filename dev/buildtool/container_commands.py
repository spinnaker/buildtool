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

"""Implements container commands for buildtool."""

import copy
import logging
import os
import yaml


from buildtool import (
    SPINNAKER_DOCKER_REGISTRY,
    SPINNAKER_RUNNABLE_REPOSITORY_NAMES,
    CommandFactory,
    CommandProcessor,
    check_options_set,
    check_path_exists,
    check_subprocess,
    raise_and_log_error,
)


class TagContainersFactory(CommandFactory):
    """Tags container images."""

    # pylint: disable=too-few-public-methods

    def __init__(self, **kwargs):
        super().__init__(
            "tag_containers",
            TagContainersCommand,
            "Tag containers with regctl.",
            **kwargs,
        )

    def init_argparser(self, parser, defaults):
        super().init_argparser(parser, defaults)

        self.add_argument(
            parser,
            "spinnaker_version",
            defaults,
            None,
            help="The new Spinnaker release version to tag containers with.",
        )

        self.add_argument(
            parser,
            "bom_path",
            defaults,
            None,
            help="The path to the local BOM file with service to version mappings.",
        )

        self.add_argument(
            parser,
            "dry_run",
            defaults,
            True,
            type=bool,
            help="Show proposed actions, don't actually do them. Default True.",
        )


class TagContainersCommand(CommandProcessor):
    """Implements tag_containers command."""

    # pylint: disable=too-few-public-methods

    def __init__(self, factory, options, **kwargs):
        check_options_set(options, ["bom_path", "spinnaker_version"])
        check_path_exists(options.bom_path, why="bom_path")

        options_copy = copy.copy(options)
        super().__init__(factory, options_copy, **kwargs)

    def __load_bom_from_path(self, path):
        """Load bom.yml from a file."""
        logging.debug("Loading bom.yml from %s", path)
        with open(path, encoding="utf-8") as f:
            bom_yaml_string = f.read()
        bom_dict = yaml.safe_load(bom_yaml_string)

        return bom_dict

    def regctl_image_copy(self, existing_image, new_image):
        """Tag Alpine and Ubuntu container images with regctl"""
        # https://github.com/google-github-actions/auth/blob/main/README.md#other-inputs
        # describes GOOGLE_GHA_CREDS_PATH
        if "GOOGLE_GHA_CREDS_PATH" in os.environ:
            creds_path = os.environ["GOOGLE_GHA_CREDS_PATH"]
            result = check_subprocess(
                f"gcloud auth activate-service-account --key-file={creds_path}"
            )
            logging.info("gcloud auth result: %s", result)

        result = check_subprocess(
            f"regctl --verbosity info image copy {existing_image} {new_image}"
        )
        logging.info("Container tag result: %s", result)

    def _do_command(self):
        """Implements CommandProcessor interface."""
        options = self.options

        bom_dict = self.__load_bom_from_path(options.bom_path)

        logging.info("Tagging containers in bom: %s", options.bom_path)

        for service in bom_dict["services"]:
            if service == "monitoring-third-party":
                continue

            version = bom_dict["services"][service]["version"]
            existing_image = (
                f"{SPINNAKER_DOCKER_REGISTRY}/{service}:{version}-unvalidated"
            )

            tag_permutations = [f"{version}", f"spinnaker-{options.spinnaker_version}"]

            if options.dry_run:
                logging.warning(
                    "SKIP tagging %s containers because --dry_run=true"
                    "\nNew tags were: %s",
                    service,
                    tag_permutations,
                )
                continue

            logging.info("Tagging container: %s(-ubuntu)", existing_image)

            for tag in tag_permutations:

                alpine_image = f"{SPINNAKER_DOCKER_REGISTRY}/{service}:{tag}"
                self.regctl_image_copy(existing_image, alpine_image)

                ubuntu_image = f"{SPINNAKER_DOCKER_REGISTRY}/{service}:{tag}-ubuntu"
                self.regctl_image_copy(f"{existing_image}-ubuntu", ubuntu_image)


def register_commands(registry, subparsers, defaults):
    """Registers all the commands for this module."""
    TagContainersFactory().register(registry, subparsers, defaults)
