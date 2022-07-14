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

"""Implements build command for buildtool."""

from multiprocessing.pool import ThreadPool

import copy
import datetime
import logging
import os
import re
import shutil
import subprocess
import textwrap
import yaml

from buildtool import (
    DEFAULT_BUILD_NUMBER,
    SPINNAKER_IO_REPOSITORY_NAME,
    SPINNAKER_HALYARD_REPOSITORY_NAME,
    BranchSourceCodeManager,
    CommandProcessor,
    CommandFactory,
    GitRunner,
    GradleCommandFactory,
    GradleCommandProcessor,
    GradleRunner,
    HalRunner,
    SpinnakerSourceCodeManager,
    run_subprocess,
    check_subprocess,
    check_subprocesses_to_logfile,
    check_options_set,
    raise_and_log_error,
    write_to_path,
    ConfigError,
    ExecutionError,
)


def build_halyard_docs(command, repository):
    """Builds Halyard's CLI and updates documentation in its repo."""
    cli_dir = os.path.join(repository.git_dir, "halyard-cli")

    # Before, we were doing this first:
    # check_run_quick('git -C halyard rev-parse HEAD'
    #                 ' | xargs git -C halyard checkout ;')
    # however now the repository should already be at the desired commit.
    logging.debug("Building Halyard CLI and docs.")
    logfile = command.get_logfile_path("build-docs")
    check_subprocesses_to_logfile("Build halyard docs", logfile, ["make"], cwd=cli_dir)


class BuildHalyardCommand(GradleCommandProcessor):
    """Implements the build_halyard command."""

    # pylint: disable=too-few-public-methods

    HALYARD_VERSIONS_BASENAME = "nightly-version-commits.yml"

    def __init__(self, factory, options, **kwargs):
        options_copy = copy.copy(options)
        options_copy.bom_path = None
        options_copy.bom_version = None
        self.__build_version = None  # recorded after build
        self.__versions_url = options.halyard_version_commits_url

        if not self.__versions_url:
            self.__versions_url = "{base}/{filename}".format(
                base=options.halyard_bucket_base_url,
                filename=self.HALYARD_VERSIONS_BASENAME,
            )
        super().__init__(
            factory,
            options_copy,
            source_repository_names=[SPINNAKER_HALYARD_REPOSITORY_NAME],
            **kwargs
        )

    def publish_halyard_version_commits(self, repository):
        """Publish the halyard build to the bucket.

        This also writes the built version to
            <output_dir>/halyard/last_version_commit.yml
        so callers can know what version was written.

        NOTE(ewiseblatt): 20180110 Halyard's policies should be revisited here.
        Although this is a "Publish" it is not a release. It is publishing
        the 'nightly' build which isnt really nightly just 'last-build',
        which could even be on an older branch than latest.
        """
        commit_id = self.source_code_manager.git.query_local_repository_commit_id(
            repository.git_dir
        )

        # This is only because we need a file to gsutil cp
        # We already need gsutil so its easier to just use it again here.
        output_dir = self.get_output_dir()
        tmp_path = os.path.join(output_dir, self.HALYARD_VERSIONS_BASENAME)

        contents = self.load_halyard_version_commits()
        new_entry = "{version}: {commit}\n".format(
            version=self.__build_version, commit=commit_id
        )

        logging.info("Updating %s with %s", self.__versions_url, new_entry)
        if contents and contents[-1] != "\n":
            contents += "\n"
        contents = contents + new_entry
        with open(tmp_path, "w") as stream:
            stream.write(contents)
        check_subprocess(
            f"gsutil cp {tmp_path} {self.__versions_url}"
        )
        self.__emit_last_commit_entry(new_entry)

    def __emit_last_commit_entry(self, entry):
        last_version_commit_path = os.path.join(
            self.get_output_dir(), "last_version_commit.yml"
        )
        write_to_path(entry, last_version_commit_path)

    def publish_to_nightly(self, repository):
        options = self.options
        cmd = "./release/promote-all.sh {version} nightly".format(
            version=self.__build_version
        )
        env = dict(os.environ)
        env.update(
            {
                "PUBLISH_HALYARD_ARTIFACT_REGISTRY_IMAGE_BASE": options.halyard_artifact_registry_image_base,
                "PUBLISH_HALYARD_BUCKET_BASE_URL": options.halyard_bucket_base_url,
                "PUBLISH_HALYARD_DOCKER_IMAGE_BASE": options.halyard_docker_image_base,
            }
        )
        logging.info(
            "Preparing the environment variables for release/all.sh:\n"
            "    PUBLISH_HALYARD_ARTIFACT_REGISTRY_IMAGE_BASE=%s\n"
            "    PUBLISH_HALYARD_DOCKER_IMAGE_BASE=%s\n"
            "    PUBLISH_HALYARD_BUCKET_BASE_URL=%s",
            options.halyard_artifact_registry_image_base,
            options.halyard_docker_image_base,
            options.halyard_bucket_base_url,
        )

        logfile = self.get_logfile_path("halyard-publish-to-nightly")
        check_subprocesses_to_logfile(
            "halyard publish to nightly",
            logfile,
            [cmd],
            cwd=repository.git_dir,
            env=env,
        )

    def build_all_halyard_deployments(self, repository):
        """Helper function for building halyard."""
        options = self.options

        git_dir = repository.git_dir
        summary = self.source_code_manager.git.collect_repository_summary(git_dir)
        self.__build_version = f"{summary.version}-{options.build_number}"

        commands = [
            self.gcloud_command(
                name="halyard-container-build",
                config_filename="containers.yml",
                git_dir=git_dir,
                substitutions={
                    "TAG_NAME": self.__build_version,
                    "_ARTIFACT_REGISTRY": options.artifact_registry,
                },
            ),
            self.gcloud_command(
                name="halyard-deb-build",
                config_filename="debs.yml",
                git_dir=git_dir,
                substitutions={
                    "_VERSION": summary.version,
                    "_BUILD_NUMBER": options.build_number,
                },
            ),
            self.gcloud_command(
                name="halyard-tar-build",
                config_filename="halyard-tars.yml",
                git_dir=git_dir,
                substitutions={"TAG_NAME": self.__build_version},
            ),
        ]

        pool = ThreadPool(len(commands))
        pool.map(self.run_gcloud_build, commands)
        pool.close()
        pool.join()

    def gcloud_command(self, name, config_filename, git_dir, substitutions):
        options = self.options
        branch = options.git_branch
        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "cloudbuild", config_filename
        )
        standard_substitutions = {
            "_BRANCH_NAME": branch,
            "_BRANCH_TAG": re.sub(r"\W", "_", branch),
            "_IMAGE_NAME": "halyard",
        }
        full_substitutions = dict(standard_substitutions, **substitutions)
        # Convert it to the format expected by gcloud: "_FOO=bar,_BAZ=qux"
        substitutions_arg = ",".join(
            "=".join((str(k), str(v))) for k, v in full_substitutions.items()
        )
        command = (
            "gcloud builds submit "
            " --account={account} --project={project}"
            " --substitutions={substitutions_arg},"
            " --config={config} .".format(
                account=options.gcb_service_account,
                project=options.gcb_project,
                substitutions_arg=substitutions_arg,
                config=config_path,
            )
        )
        return {"name": name, "git_dir": git_dir, "command": command}

    def run_gcloud_build(self, command):
        logfile = self.get_logfile_path(command["name"])
        self.metrics.time_call(
            "GcrBuild",
            {},
            self.metrics.default_determine_outcome_labels,
            check_subprocesses_to_logfile,
            command["name"],
            logfile,
            [command["command"]],
            cwd=command["git_dir"],
        )

    def load_halyard_version_commits(self):
        logging.debug("Fetching existing halyard build versions")
        retcode, stdout = run_subprocess("gsutil cat " + self.__versions_url)
        if not retcode:
            contents = stdout + "\n"
        else:
            if stdout.find("No URLs matched") < 0:
                raise_and_log_error(
                    ExecutionError("No URLs matched", program="gsutil"),
                    f'Could not fetch "{self.__versions_url}": {stdout}',
                )
            contents = ""
            logging.warning(
                "%s did not exist. Creating a new one.", self.__versions_url
            )
        return contents

    def find_commit_version_entry(self, repository):
        logging.debug("Looking for existing halyard version for this commit.")
        if os.path.exists(repository.git_dir):
            commit_id = self.git.query_local_repository_commit_id(repository.git_dir)
        else:
            commit_id = self.git.query_remote_repository_commit_id(
                repository.origin, self.options.git_branch
            )
        commits = self.load_halyard_version_commits().split("\n")
        commits.reverse()
        postfix = " " + commit_id
        for line in commits:
            if line.endswith(postfix):
                return line
        return None

    def _do_can_skip_repository(self, repository):
        if self.options.skip_existing:
            entry = self.find_commit_version_entry(repository)
            if entry:
                logging.info('Found existing halyard version "%s"', entry)
                labels = {"repository": repository.name, "artifact": "halyard"}
                self.metrics.inc_counter("ReuseArtifact", labels)
                self.__emit_last_commit_entry(entry)
                return True
        return False

    def _do_repository(self, repository):
        """Implements RepositoryCommandProcessor interface."""
        # The gradle prepare for nebula needs the build version
        # which it will get from the source info, so make it here.
        source_info = self.source_code_manager.refresh_source_info(
            repository, self.options.build_number
        )

        # I guess we build the docs just as a test? We never put them anywhere...
        # PublishHalyardCommand _does_ actually publish them after building, so I
        # suppose we're just making sure we don't die at publish time
        build_halyard_docs(self, repository)
        self.build_all_halyard_deployments(repository)
        self.publish_to_nightly(repository)
        self.publish_halyard_version_commits(repository)


class BuildHalyardFactory(GradleCommandFactory):
    """Implements the build_halyard command."""

    # pylint: disable=too-few-public-methods

    def __init__(self):
        super().__init__(
            "build_halyard",
            BuildHalyardCommand,
            "Build halyard from the local git repository.",
            BranchSourceCodeManager,
        )

    def init_argparser(self, parser, defaults):
        """Adds command-specific arguments."""
        super().init_argparser(parser, defaults)

        self.add_argument(
            parser,
            "halyard_version_commits_url",
            defaults,
            None,
            help="URL to file containing version and git commit for successful"
            " nightly builds. By default this will be"
            ' "{filename}" in the'
            " --halyard_bucket_base_url.".format(
                filename=BuildHalyardCommand.HALYARD_VERSIONS_BASENAME
            ),
        )
        self.add_argument(
            parser,
            "build_number",
            defaults,
            DEFAULT_BUILD_NUMBER,
            help="The build number is used when generating halyard.",
        )
        self.add_argument(
            parser,
            "halyard_bucket_base_url",
            defaults,
            None,
            help="Base Google Cloud Storage URL for writing halyard builds.",
        )
        self.add_argument(
            parser,
            "halyard_docker_image_base",
            defaults,
            None,
            help="Base Docker image name for writing halyard builds.",
        )
        self.add_argument(
            parser,
            "halyard_artifact_registry_image_base",
            defaults,
            None,
            help="Base Artifact Registry image name for writing halyard builds.",
        )
        self.add_argument(
            parser,
            "gcb_project",
            defaults,
            None,
            help="The GCP project ID when using the GCP Container Builder.",
        )
        self.add_argument(
            parser,
            "gcb_service_account",
            defaults,
            None,
            help="Google Service Account when using the GCP Container Builder.",
        )
        self.add_argument(
            parser,
            "artifact_registry",
            defaults,
            None,
            help="Artifact registry to push the container images to.",
        )


class PublishHalyardCommandFactory(CommandFactory):
    def __init__(self):
        super().__init__(
            "publish_halyard", PublishHalyardCommand, "Publish a new halyard release."
        )

    def init_argparser(self, parser, defaults):
        super().init_argparser(parser, defaults)
        GradleCommandFactory.add_bom_parser_args(parser, defaults)
        SpinnakerSourceCodeManager.add_parser_args(parser, defaults)
        GradleRunner.add_parser_args(parser, defaults)
        GitRunner.add_publishing_parser_args(parser, defaults)
        HalRunner.add_parser_args(parser, defaults)

        self.add_argument(
            parser,
            "build_number",
            defaults,
            DEFAULT_BUILD_NUMBER,
            help="Publishing halyard requires a rebuild. This is the build number"
            " to use when rebuilding halyard.",
        )

        self.add_argument(
            parser,
            "halyard_version",
            defaults,
            None,
            help="The semantic version of the release to publish.",
        )

        self.add_argument(
            parser,
            "halyard_version_commits_url",
            defaults,
            None,
            help="URL to file containing version and git commit for successful"
            " nightly builds. By default this will be"
            ' "{filename}" in the'
            " --halyard_bucket_base_url.".format(
                filename=BuildHalyardCommand.HALYARD_VERSIONS_BASENAME
            ),
        )
        self.add_argument(
            parser,
            "halyard_docker_image_base",
            defaults,
            None,
            help="Base Docker image name for writing halyard builds.",
        )
        self.add_argument(
            parser,
            "halyard_artifact_registry_image_base",
            defaults,
            None,
            help="Base Artifact Registry image name for writing halyard builds.",
        )
        self.add_argument(
            parser,
            "halyard_bucket_base_url",
            defaults,
            None,
            help="Base Google Cloud Storage URL for writing halyard builds.",
        )

        self.add_argument(
            parser,
            "docs_repo_owner",
            defaults,
            None,
            help="Owner of the docs repo if one was"
            " specified. The default is --github_owner.",
        )
        self.add_argument(
            parser,
            "skip_existing",
            defaults,
            False,
            type=bool,
            help="Skip builds if the desired version already exists on bintray.",
        )

        self.add_argument(
            parser,
            "delete_existing",
            defaults,
            None,
            type=bool,
            help="Delete pre-existing desired versions if from bintray.",
        )

        self.add_argument(
            parser,
            "gcb_project",
            defaults,
            None,
            help="The GCP project ID when using the GCP Container Builder.",
        )
        self.add_argument(
            parser,
            "gcb_service_account",
            defaults,
            None,
            help="Google Service Account when using the GCP Container Builder.",
        )
        self.add_argument(
            parser,
            "artifact_registry",
            defaults,
            None,
            help="Artifact Registry to push the container images to.",
        )


class PublishHalyardCommand(CommandProcessor):
    """Publish halyard version to the public repository."""

    def __init__(self, factory, options, **kwargs):
        options_copy = copy.copy(options)
        options_copy.bom_path = None
        options_copy.bom_version = None
        options_copy.git_branch = "master"
        options_copy.github_hostname = "github.com"
        # Overrides later if --git_allow_publish_master_branch is false
        super().__init__(factory, options_copy, **kwargs)

        check_options_set(options, ["halyard_version"])
        match = re.match(r"(\d+)\.(\d+)\.(\d+)-\d+", options.halyard_version)
        if match is None:
            raise_and_log_error(
                ConfigError(
                    "--halyard_version={version} is not X.Y.Z-<buildnum>".format(
                        version=options.halyard_version
                    )
                )
            )
        self.__stable_version = "{major}.{minor}.{patch}".format(
            major=match.group(1), minor=match.group(2), patch=match.group(3)
        )

        self.__scm = BranchSourceCodeManager(options_copy, self.get_input_dir())
        self.__hal = HalRunner(options_copy)
        self.__gradle = GradleRunner(options_copy, self.__scm, self.metrics)
        self.__halyard_repo_md_path = os.path.join("docs", "commands.md")

        dash = self.options.halyard_version.find("-")
        semver_str = self.options.halyard_version[0:dash]
        semver_parts = semver_str.split(".")
        if len(semver_parts) != 3:
            raise_and_log_error(
                ConfigError("Expected --halyard_version in the form X.Y.Z-N")
            )
        self.__release_branch = "release-{maj}.{min}.x".format(
            maj=semver_parts[0], min=semver_parts[1]
        )
        self.__release_tag = "version-" + semver_str
        self.__release_version = semver_str

    def determine_halyard_commit(self):
        """Determine the commit_id that we want to publish."""
        options = self.options
        versions_url = options.halyard_version_commits_url
        if not versions_url:
            versions_url = "{base}/{filename}".format(
                base=options.halyard_bucket_base_url,
                filename=BuildHalyardCommand.HALYARD_VERSIONS_BASENAME,
            )

        if os.path.exists(versions_url):
            logging.debug("Loading halyard version info from file %s", versions_url)
            with open(versions_url) as stream:
                version_data = stream.read()
        else:
            logging.debug("Loading halyard version info from bucket %s", versions_url)
            gsutil_output = check_subprocess(
                f"gsutil cat {versions_url}", stderr=subprocess.PIPE
            )

            # The latest version of gsutil prints a bunch of python warnings to stdout
            # (see b/152449160). This file is a series of lines that look like...
            #   0.41.0-180209172926: 05f1e832ab438e5a980d1102e84cdb348a0ab055
            # ...so we'll just throw out any lines that don't start with digits.
            valid_lines = [
                line for line in gsutil_output.splitlines() if line[0].isdigit()
            ]
            version_data = "\n".join(valid_lines)

        commit = yaml.safe_load(version_data).get(options.halyard_version)
        if commit is None:
            raise_and_log_error(
                ConfigError(
                    'Unknown halyard version "{version}" in "{url}"'.format(
                        version=options.halyard_version, url=versions_url
                    )
                )
            )
        return commit

    def _prepare_repository(self):
        """Prepare a local repository to build for release.

        Were rebuilding it only to have nebula give a new distribution tag.
        However we will also use the repository to tag and branch the release
        into github so want to at least clone the repo regardless.
        """
        logging.debug("Preparing repository for publishing a halyard release.")
        commit = self.determine_halyard_commit()
        repository = self.__scm.make_repository_spec(
            SPINNAKER_HALYARD_REPOSITORY_NAME, commit_id=commit
        )
        git_dir = repository.git_dir
        if os.path.exists(git_dir):
            logging.info("Deleting existing %s to build fresh.", git_dir)
            shutil.rmtree(git_dir)
        git = self.__scm.git
        git.clone_repository_to_path(repository, commit=commit)
        return repository

    def _promote_halyard(self, repository):
        """Promote an existing build to become the halyard stable version."""
        options = self.options
        logfile = self.get_logfile_path("promote-all")
        env = dict(os.environ)
        env.update(
            {
                "PUBLISH_HALYARD_ARTIFACT_DOCKER_IMAGE_SRC_BASE": options.halyard_artifact_registry_image_base,
                "PUBLISH_HALYARD_BUCKET_BASE_URL": options.halyard_bucket_base_url,
                "PUBLISH_HALYARD_DOCKER_IMAGE_BASE": options.halyard_docker_image_base,
            }
        )
        check_subprocesses_to_logfile(
            "Promote Halyard",
            logfile,
            [
                "gcloud docker -a",  # if repo is private it needs authenticated
                "./release/promote-all.sh {candidate} {stable}".format(
                    candidate=options.halyard_version, stable=self.__stable_version
                ),
                "./release/promote-all.sh {candidate} stable".format(
                    candidate=options.halyard_version
                ),
            ],
            env=env,
            cwd=repository.git_dir,
        )

    def _build_release(self, repository):
        """Rebuild the actual release debian package.

        We dont necessarily need to rebuild here. We just need to push as
        debian to the "-stable". However there isnt an easy way to do this.

        Note that this is not the promoted version. For safety[*] and simplicity
        we'll promote the candidate whose version was used to build this.
        Ideally this function can go away.

        [*] Safety because the candidate was tested whereas this build was not.
        """
        # Ideally we would just modify the existing bintray version to add
        # *-stable to the distributions, however it does not appear possible
        # to patch the debian attributes of a bintray version, only the
        # version metadata. Therefore, we'll rebuild it.
        # Alternatively we could download the existing and push a new one,
        # however I dont see how to get at the existing debian metadata and
        # dont want to ommit something

        options = self.options
        git_dir = repository.git_dir
        summary = self.__scm.git.collect_repository_summary(git_dir)

        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "cloudbuild", "debs.yml"
        )
        substitutions = {
            "_BRANCH_NAME": options.git_branch,
            "_BRANCH_TAG": re.sub(r"\W", "_", options.git_branch),
            "_BUILD_NUMBER": options.build_number,
            "_IMAGE_NAME": "halyard",
            "_VERSION": summary.version,
        }
        # Convert it to the format expected by gcloud: "_FOO=bar,_BAZ=qux"
        substitutions_arg = ",".join(
            "=".join((str(k), str(v))) for k, v in substitutions.items()
        )
        command = (
            "gcloud builds submit "
            " --account={account} --project={project}"
            " --substitutions={substitutions_arg},"
            " --config={config} .".format(
                account=options.gcb_service_account,
                project=options.gcb_project,
                substitutions_arg=substitutions_arg,
                config=config_path,
            )
        )
        logfile = self.get_logfile_path("build-deb")
        check_subprocesses_to_logfile(
            "building deb with published version", logfile, [command], cwd=git_dir
        )

    def write_target_docs(self, source_repository, target_repository):
        source_path = os.path.join(
            source_repository.git_dir, self.__halyard_repo_md_path
        )
        target_rel_path = os.path.join("reference", "halyard", "commands.md")
        target_path = os.path.join(target_repository.git_dir, target_rel_path)
        now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        logging.debug("Writing documentation into %s", target_path)
        header = textwrap.dedent(
            """\
        ---
        layout: single
        title: "Commands"
        sidebar:
          nav: reference
        ---
        Published: {now}
        """.format(
                now=now
            )
        )
        with open(source_path) as source:
            body = source.read()
        with open(target_path, "w") as stream:
            stream.write(header)
            stream.write(body)
        return target_rel_path

    def push_docs(self, repository):
        base_branch = "master"
        target_repository = self.__scm.make_repository_spec(
            SPINNAKER_IO_REPOSITORY_NAME
        )
        self.__scm.ensure_git_path(target_repository)
        target_rel_path = self.write_target_docs(repository, target_repository)

        if self.options.git_allow_publish_master_branch:
            head_branch = "master"
            branch_flag = ""
        else:
            head_branch = self.__release_version + "-haldocs"
            branch_flag = "-b"
        logging.debug(
            'Commiting changes into local repository "%s" branch=%s',
            target_repository.git_dir,
            head_branch,
        )

        git_dir = target_repository.git_dir
        message = "docs(halyard): " + self.__release_version
        local_git_commands = [
            # These commands are accomodating to a branch already existing
            # because the branch is on the version, not build. A rejected
            # build for some reason that is re-tried will have the same version
            # so the branch may already exist from the earlier attempt.
            "checkout " + base_branch,
            f"checkout {branch_flag} {head_branch}",
            "add " + target_rel_path,
        ]

        logging.debug(
            'Commiting changes into local repository "%s" branch=%s',
            target_repository.git_dir,
            head_branch,
        )
        git = self.__scm.git
        git.check_run_sequence(git_dir, local_git_commands)
        git.check_commit_or_no_changes(
            git_dir, f'-m "{message}" {target_rel_path}'
        )

        logging.info(
            'Pushing halyard docs to %s branch="%s"',
            target_repository.origin,
            head_branch,
        )
        git.push_branch_to_origin(
            target_repository.git_dir, branch=head_branch, force=True
        )

    def _do_command(self):
        """Implements CommandProcessor interface."""
        repository = self._prepare_repository()
        # Removing debian publishing, until we have a new place to push them to.
        # self._build_release(repository)
        self._promote_halyard(repository)
        build_halyard_docs(self, repository)
        self.push_docs(repository)
        self.push_tag_and_branch(repository)
        self.__hal.publish_halyard_release(self.__release_version)

    def push_tag_and_branch(self, repository):
        """Pushes a stable branch and git version tag to the origin repository."""
        git_dir = repository.git_dir
        git = self.__scm.git

        release_url = git.determine_push_url(repository.origin)
        logging.info(
            "Pushing branch=%s and tag=%s to %s",
            self.__release_branch,
            self.__release_tag,
            release_url,
        )

        existing_commit = git.query_commit_at_tag(git_dir, self.__release_tag)
        if existing_commit:
            want_commit = git.query_local_repository_commit_id(git_dir)
            if want_commit == existing_commit:
                logging.debug(
                    'Already have "%s" at %s', self.__release_tag, want_commit
                )
                return

        git.check_run_sequence(
            git_dir,
            [
                "checkout -b " + self.__release_branch,
                "remote add release " + release_url,
                "push release " + self.__release_branch,
                "tag " + self.__release_tag,
                "push release " + self.__release_tag,
            ],
        )


def register_commands(registry, subparsers, defaults):
    BuildHalyardFactory().register(registry, subparsers, defaults)
    PublishHalyardCommandFactory().register(registry, subparsers, defaults)
