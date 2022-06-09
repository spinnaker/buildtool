# Copyright 2019 Microsoft Inc. All Rights Reserved.
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

"""Azure platform and test support for SpinnakerTestScenario."""

import citest.azure_testing as az
from spinnaker_testing.base_scenario_support import BaseScenarioPlatformSupport


class AzureScenarioSupport(BaseScenarioPlatformSupport):
    """Provides SpinnakerScenarioSupport for Azure."""

    @classmethod
    def add_commandline_parameters(cls, scenario_class, builder, defaults):
        """Implements BaseScenarioPlatformSupport interface.

        Args:
          scenario_class: [class spinnaker_testing.SpinnakerTestScenario]
          builder: [citest.base.ConfigBindingsBuilder]
          defaults: [dict] Default binding value overrides.
             This is used to initialize the default commandline parameters.
        """
        #
        # Operation Parameters
        #
        builder.add_argument(
            "--test_azure_rg_location",
            default=defaults.get("TEST_AZURE_RG_LOCATION", "westus"),
            help="The location of the azure resource group where test resources should be created.",
        )
        builder.add_argument(
            "--test_azure_resource_group",
            default=defaults.get("TEST_AZURE_RESOURCE_GROUP", None),
            help="The name of azure resource group where test resources should be created.",
        )
        builder.add_argument(
            "--test_azure_subscription_id",
            default=defaults.get("TEST_AZURE_SUBSCRIPTION_ID", None),
            help="The subscription id of your azure account",
        )
        builder.add_argument(
            "--test_azure_vnet",
            help="The name of the virtual network that contains the subnets",
        )
        builder.add_argument(
            "--test_azure_subnets",
            help="The names and addresses of subnets separated by comma",
        )
        builder.add_argument(
            "--test_azure_vm_sku",
            default=defaults.get("TEST_AZURE_VM_SKU", "Standard_B1ms"),
            help="The name of VMSS",
        )
        builder.add_argument(
            "--test_azure_baseOS",
            default=defaults.get("TEST_AZURE_BASEOS", "ubuntu-1604"),
            help="The OS version of the cluster used for deploying",
        )
        builder.add_argument(
            "--test_azure_OSType",
            default=defaults.get("TEST_AZURE_OSTYPE", "linux"),
            help="The OS type of the cluster used for deploying",
        )
        builder.add_argument(
            "--azure_storage_account_name",
            dest="azure_storage_account_name",
            help="The name of the Azure storage account used by front50 in Spinnaker.",
        )
        builder.add_argument(
            "--azure_storage_account_key",
            dest="spinnaker_azure_storage_account_key",
            help="The key of the Azure storage account used by front50 in Spinnaker.",
        )

    def _make_observer(self):
        """Implements BaseScenarioPlatformSupport interface."""
        bindings = self.scenario.bindings
        if not bindings.get("TEST_AZURE_RG_LOCATION"):
            raise ValueError("There is no location specified")

        return az.AzAgent()

    def __init__(self, scenario):
        """Constructor.

        Args:
          scenario: [SpinnakerTestScenario] The scenario being supported.
        """
        super(AzureScenarioSupport, self).__init__("azure", scenario)
        self.__az_observer = None

        bindings = scenario.bindings
        if not bindings["SPINNAKER_AZURE_ACCOUNT"]:
            bindings["SPINNAKER_AZURE_ACCOUNT"] = scenario.agent.deployed_config.get(
                "providers.azure.primaryCredentials.name", None
            )
