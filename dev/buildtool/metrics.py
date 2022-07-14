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

"""Metrics support manager."""

import logging

from buildtool import add_parser_argument
from buildtool.inmemory_metrics import InMemoryMetricsRegistry
from buildtool.influxdb_metrics import InfluxDbMetricsRegistry


class MetricsManager:
    """Acts as factory for specialized BaseMetricsRegistry singleton."""

    __metrics_registry = None

    @staticmethod
    def singleton():
        """Returns the BaseMetricsRegistry once startup_metrics is called."""
        if MetricsManager.__metrics_registry is None:
            raise Exception("startup_metrics was not called.")
        return MetricsManager.__metrics_registry

    @staticmethod
    def init_argument_parser(parser, defaults):
        """Init argparser with metrics-related options."""
        InMemoryMetricsRegistry.init_argument_parser(parser, defaults)
        InfluxDbMetricsRegistry.init_argument_parser(parser, defaults)
        add_parser_argument(
            parser,
            "monitoring_enabled",
            defaults,
            False,
            type=bool,
            help="Enable monitoring to stackdriver.",
        )
        add_parser_argument(
            parser,
            "monitoring_flush_frequency",
            defaults,
            15,
            help="Frequency at which to push metrics in seconds.",
        )
        add_parser_argument(
            parser,
            "monitoring_system",
            defaults,
            "file",
            choices=["file", "influxdb"],
            help="Where to store metrics.",
        )
        add_parser_argument(
            parser,
            "monitoring_context_labels",
            defaults,
            None,
            help="A comma-separated list of additional name=value"
            " labels to add to each event to associate them together."
            " (e.g. version=release-1.2.x)",
        )

    @staticmethod
    def startup_metrics(options):
        """Startup metrics module with concrete system."""
        monitoring_systems = {
            "file": InMemoryMetricsRegistry,
            "influxdb": InfluxDbMetricsRegistry,
        }
        klas = monitoring_systems[options.monitoring_system]
        logging.debug('Initializing monitoring with system="%s"', klas.__name__)
        MetricsManager.__metrics_registry = klas(options)
        if options.monitoring_enabled and options.monitoring_flush_frequency > 0:
            MetricsManager.__metrics_registry.start_pusher_thread()
        return MetricsManager.__metrics_registry

    @staticmethod
    def shutdown_metrics():
        """Write final metrics out to metrics server."""
        registry = MetricsManager.singleton()
        registry.stop_pusher_thread()
        registry.flush_updated_metrics()
        registry.flush_final_metrics()
