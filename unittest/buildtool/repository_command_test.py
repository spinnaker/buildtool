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

import logging
import os
import threading
import time
import unittest


from buildtool import (
    RepositoryCommandProcessor,
    RepositoryCommandFactory,
    BranchSourceCodeManager,
)

import custom_test_command


from test_util import (
    ALL_STANDARD_TEST_BOM_REPO_NAMES,
    OUTLIER_REPO,
    BaseGitRepoTestFixture,
    init_runtime,
)


COMMAND = custom_test_command.COMMAND
FAILURE_COMMAND_NAME = "test_command_failure"


class TestRepositoryCommand(RepositoryCommandProcessor):
    def __init__(
        self, factory, options, pos_arg, source_repository_names=None, **kwargs
    ):
        super().__init__(
            factory, options, source_repository_names=source_repository_names
        )
        self.test_init_args = (factory, options, pos_arg, kwargs)
        self.preprocessed = False
        self.postprocess_dict = None
        self.ensured = set()
        self.repositories = set()
        self.test_repo_threadid = []

    def ensure_local_repository(self, repository):
        assert self.preprocessed
        assert not self.postprocess_dict
        assert repository.name not in self.ensured
        assert repository.name not in self.repositories
        self.ensured.add(repository.name)

    def _do_preprocess(self):
        assert not self.preprocessed
        self.preprocessed = True

    def _do_postprocess(self, result_dict):
        assert self.preprocessed
        assert not self.postprocess_dict
        self.postprocess_dict = result_dict
        return {"foo": "bar"}

    def _do_repository(self, repository):
        assert self.preprocessed
        assert not self.postprocess_dict
        assert repository.name in self.ensured
        assert repository.name not in self.repositories
        self.repositories.add(repository.name)
        time.sleep(0.1)  # Encourage threads to be different
        self.test_repo_threadid.append(threading.current_thread().ident)
        if self.name == FAILURE_COMMAND_NAME:
            logging.info("Raising injected error")
            raise ValueError("Injected Failure")
        return f"TEST {repository.name}"


class RepositoryCommandProcessorTest(BaseGitRepoTestFixture):
    def make_test_options(self):
        options = super().make_test_options()
        options.git_branch = "test_branch"
        options.github_hostname = "test-hostname"
        options.github_owner = "test_github_owner"
        options.github_pull_ssh = False
        options.one_at_a_time = False
        options.only_repositories = None
        options.exclude_repositories = None
        options.github_upstream_owner = None
        return options

    def test_filter(self):
        factory = RepositoryCommandFactory(
            "test_filter",
            TestRepositoryCommand,
            "A test command.",
            BranchSourceCodeManager,
            123,
            source_repository_names=ALL_STANDARD_TEST_BOM_REPO_NAMES,
        )
        options = self.options

        expected_repo_names = list(ALL_STANDARD_TEST_BOM_REPO_NAMES)
        command = factory.make_command(options)
        self.assertEqual(
            expected_repo_names,
            [repository.name for repository in command.source_repositories],
        )

        expected_repo_names = [
            name for name in ALL_STANDARD_TEST_BOM_REPO_NAMES if name != OUTLIER_REPO
        ]
        options.exclude_repositories = OUTLIER_REPO
        command = factory.make_command(options)
        self.assertEqual(
            expected_repo_names,
            [repository.name for repository in command.source_repositories],
        )

        expected_repo_names = [OUTLIER_REPO]
        options.only_repositories = OUTLIER_REPO
        options.exclude_repositories = None
        command = factory.make_command(options)
        self.assertEqual(
            expected_repo_names,
            [repository.name for repository in command.source_repositories],
        )

    def do_test_command(self, options, command_name):
        init_dict = {"a": "A", "b": "B"}
        factory = RepositoryCommandFactory(
            command_name,
            TestRepositoryCommand,
            "A test command.",
            BranchSourceCodeManager,
            123,
            source_repository_names=ALL_STANDARD_TEST_BOM_REPO_NAMES,
            **init_dict
        )

        # Test construction
        command = factory.make_command(options)
        self.assertEqual((factory, options, 123, init_dict), command.test_init_args)
        self.assertFalse(command.preprocessed)
        self.assertEqual(factory, command.factory)
        self.assertEqual(factory.name, command.name)
        key_func = lambda repo: repo.name
        expect_list = [
            command.source_code_manager.make_repository_spec(name)
            for name in ALL_STANDARD_TEST_BOM_REPO_NAMES
        ]
        self.assertEqual(
            sorted(expect_list, key=key_func),
            sorted(command.source_repositories, key=key_func),
        )
        self.assertEqual(
            os.path.join(options.input_dir, options.command),
            command.source_code_manager.root_source_dir,
        )

        # Test invocation
        if command_name == FAILURE_COMMAND_NAME:
            with self.assertRaises(ValueError):
                command()
            self.assertIsNone(command.postprocess_dict)
        else:
            self.assertEqual({"foo": "bar"}, command())
            self.assertEqual(
                command.postprocess_dict,
                {name: "TEST " + name for name in ALL_STANDARD_TEST_BOM_REPO_NAMES},
            )

        self.assertTrue(command.preprocessed)
        self.assertEqual(
            {repo.name for repo in command.source_repositories}, command.ensured
        )
        self.assertEqual(command.ensured, command.repositories)

        return command

    def test_concurrent_command(self):
        test_command_name = "concurrent_command"
        options = self.options
        options.command = test_command_name
        command = self.do_test_command(options, test_command_name)
        self.assertNotEqual(
            command.test_repo_threadid[0], command.test_repo_threadid[1]
        )

        outcome_name = "RunCommand_Outcome"
        family = command.metrics.lookup_family_or_none(outcome_name)
        self.assertIsNotNone(family)
        self.assertEqual(family.family_type, family.TIMER)
        self.assertEqual(family.name, outcome_name)
        self.assertEqual(1, len(family.instance_list))
        metric = next(iter(family.instance_list), None)
        self.assertEqual(1, metric.count)
        self.assertEqual(True, metric.labels.get("success"))
        self.assertEqual(test_command_name, metric.labels.get("command"))

        outcome_name = "RunRepositoryCommand_Outcome"
        family = command.metrics.lookup_family_or_none(outcome_name)
        self.assertIsNotNone(family)
        self.assertEqual(family.family_type, family.TIMER)
        self.assertEqual(family.name, outcome_name)
        repository_names = {
                metric.labels.get("repository")
                for metric in family.instance_list
                if metric.labels.get("command") == test_command_name
        }
        self.assertEqual(2, len(repository_names))
        self.assertEqual(repository_names, set(ALL_STANDARD_TEST_BOM_REPO_NAMES))
        for metric in family.instance_list:
            self.assertEqual(family, metric.family)
            self.assertEqual(outcome_name, metric.name)
            self.assertEqual(1, metric.count)
            self.assertEqual(True, metric.labels.get("success"))

    def test_serialized_command(self):
        test_command_name = "serialized_command"
        options = self.options
        options.one_at_a_time = True
        options.command = test_command_name
        command = self.do_test_command(options, test_command_name)
        self.assertEqual(command.test_repo_threadid[0], command.test_repo_threadid[1])

    def test_failed_command(self):
        test_command_name = FAILURE_COMMAND_NAME
        options = self.options
        options.command = test_command_name
        command = self.do_test_command(options, test_command_name)
        self.assertNotEqual(
            command.test_repo_threadid[0], command.test_repo_threadid[1]
        )

        for test_repository_command in [True, False]:
            outcome_name = (
                "RunRepositoryCommand_Outcome"
                if test_repository_command
                else "RunCommand_Outcome"
            )
            family = command.metrics.lookup_family_or_none(outcome_name)
            self.assertIsNotNone(family)
            self.assertEqual(family.family_type, family.TIMER)
            self.assertEqual(family.name, outcome_name)
            if test_repository_command:
                repository_names = {
                        metric.labels.get("repository")
                        for metric in family.instance_list
                        if metric.labels.get("command") == test_command_name
                }
                self.assertEqual(2, len(repository_names))
                self.assertEqual(
                    repository_names, set(ALL_STANDARD_TEST_BOM_REPO_NAMES)
                )

            found = False
            for metric in family.instance_list:
                if metric.labels.get("command") != test_command_name:
                    continue  # Metric was from a different test
                found = True
                self.assertEqual(family, metric.family)
                self.assertEqual(outcome_name, metric.name)
                self.assertEqual(1, metric.count)
                self.assertEqual(False, metric.labels.get("success"))
            self.assertTrue(found)


if __name__ == "__main__":
    init_runtime()
    unittest.main(verbosity=2)
