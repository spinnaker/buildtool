# Copyright 2020 Armory Inc. All Rights Reserved.
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

"""
This test exists to demonstrate that a stage implemented by a plugin
can be loaded and executed.

There's nothing plugin-specific about this test; it relies on Spinnaker
having been configured for the stage plugin correctly.
"""

# Standard python modules.
import json
import sys

# citest modules.
import citest.json_contract as jc
import citest.service_testing as st
import citest.base

# Spinnaker modules.
import spinnaker_testing as sk
import spinnaker_testing.gate as gate


class PluginStageTestScenario(sk.SpinnakerTestScenario):
    """Defines the scenario for testing a stage implemented as a plugin."""

    @classmethod
    def new_agent(cls, bindings):
        """Implements citest.service_testing.AgentTestScenario.new_agent."""
        agent = gate.new_agent(bindings)
        return agent

    @classmethod
    def initArgumentParser(cls, parser, defaults=None):
        """Initialize command line argument parser.

        Args:
          parser: argparse.ArgumentParser
        """
        super().initArgumentParser(
            parser, defaults=defaults
        )

        defaults = defaults or {}
        parser.add_argument(
            "--stage_name", default="randomWait", help="The name of the plugin stage."
        )

        parser.add_argument(
            "--stage_params",
            default='{"maxWaitTime": 5}',
            help="The stage params as a JSON-encoded string.",
        )

    def __init__(self, bindings, agent=None):
        """Constructor.

        Args:
          bindings: [dict] The data bindings to use to configure the scenario.
          agent: [GateAgent] The agent for invoking the test operations on Gate.
        """
        super().__init__(bindings, agent)

        self.STAGE_NAME = bindings["STAGE_NAME"]
        self.STAGE_PARAMS = json.loads(bindings["STAGE_PARAMS"])
        self.TEST_APP = "app-{stage}".format(stage=bindings["STAGE_NAME"])

    def run_stage_as_task(self):
        """Runs the configured stage as a Spinnaker task."""
        stage = {
            "type": self.STAGE_NAME,
            "user": "[anonymous]",
        }
        stage.update(self.STAGE_PARAMS)

        payload = self.agent.make_json_payload_from_kwargs(
            job=[stage],
            description="Execute plugin stage type {stage}".format(
                stage=self.STAGE_NAME
            ),
            application=self.TEST_APP,
        )

        return st.OperationContract(
            self.new_post_operation(
                title="execute_plugin_stage", data=payload, path="tasks"
            ),
            contract=jc.Contract(),
        )


class PluginStageTest(st.AgentTestCase):
    """The test fixture for the PluginStageTest.

    This is implemented using citest OperationContract instances that are
    created by the PluginStageTestScenario.
    """

    @property
    def scenario(self):
        return citest.base.TestRunner.global_runner().get_shared_data(
            PluginStageTestScenario
        )

    def test_run_stage(self):
        self.run_test_case(
            self.scenario.run_stage_as_task(), retry_interval_secs=5, max_retries=5
        )


def main():
    """Implements the main method running the stage plugin test."""

    return citest.base.TestRunner.main(
        parser_inits=[PluginStageTestScenario.initArgumentParser],
        default_binding_overrides={},
        test_case_list=[PluginStageTest],
    )


if __name__ == "__main__":
    sys.exit(main())
