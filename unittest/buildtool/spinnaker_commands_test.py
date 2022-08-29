# Copyright 2018 Google Inc. All Rights Reserved.
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

import argparse
import datetime
import os
import tempfile
import textwrap
import unittest

import yaml

import buildtool.__main__ as bomtool_main
import buildtool.spinnaker_commands
from buildtool import GitRunner

from test_util import EXTRA_REPO, init_runtime, BaseGitRepoTestFixture


class TestSpinnakerCommandFixture(BaseGitRepoTestFixture):
    def setUp(self):
        super().setUp()
        self.parser = argparse.ArgumentParser()
        self.subparsers = self.parser.add_subparsers(title="command", dest="command")


if __name__ == "__main__":
    init_runtime()
    unittest.main(verbosity=2)
