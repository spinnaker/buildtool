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

"""Metrics support via influxdb.

https://docs.influxdata.com/influxdb/v1.4/write_protocols/line_protocol_tutorial/

To install influx-db
  curl -sL https://repos.influxdata.com/influxdb.key \
      | sudo apt-key add - source /etc/lsb-release
  source /etc/lsb-release
  echo "deb https://repos.influxdata.com/${DISTRIB_ID,,} ${DISTRIB_CODENAME} stable" \
      | sudo tee /etc/apt/sources.list.d/influxdb.list
  sudo apt-get update && sudo apt-get install influxdb -y
  sudo service influxdb start

To create a database:
  curl -i -XPOST http://localhost:8086/query --data-urlencode \
      "q=CREATE DATABASE SpinnakerBuildTool"
"""


import datetime
import logging

try:
    from urllib2 import urlopen, Request
except ImportError:
    from urllib.request import urlopen, Request

from buildtool import add_parser_argument
from buildtool.inmemory_metrics import InMemoryMetricsRegistry


EPOCH = datetime.datetime(1970, 1, 1)
SECONDS_PER_DAY = 24 * 60 * 60
NANOS_PER_SECOND = 1000000000


def to_timestamp(utc):
    """Convert UTC datetime into epoch timestamp in nanoseconds for influxdb."""
    time_delta = utc - EPOCH
    epoch_secs = time_delta.seconds + time_delta.days * SECONDS_PER_DAY
    epoch_nanos = epoch_secs * NANOS_PER_SECOND + time_delta.microseconds * 1000
    return epoch_nanos


class InfluxDbMetricsRegistry(InMemoryMetricsRegistry):
    @staticmethod
    def init_argument_parser(parser, defaults):
        InMemoryMetricsRegistry.init_argument_parser(parser, defaults)
        add_parser_argument(
            parser,
            "influxdb_url",
            defaults,
            "http://localhost:8086",
            help="Server address to push metrics to.",
        )
        add_parser_argument(
            parser,
            "influxdb_database",
            defaults,
            "SpinnakerBuildTool",
            help="Influxdb to push metrics to.",
        )
        add_parser_argument(
            parser,
            "influxdb_reiterate_gauge_secs",
            defaults,
            60,
            help="Reiterate gauge values for the specified period of seconds."
            " This is because when they get chunked into time blocks, the"
            "values become lost, in particular settling back to 0.",
        )

    def __init__(self, *pos_args, **kwargs):
        super(InfluxDbMetricsRegistry, self).__init__(*pos_args, **kwargs)
        self.__export_func_map = {
            "COUNTER": self.__export_counter_points,
            "GAUGE": self.__export_gauge_points,
            "TIMER": self.__export_timer_points,
        }
        self.__recent_gauges = set([])

    def _do_flush_final_metrics(self):
        """Implements interface."""
        self.flush_updated_metrics()

    def _do_flush_updated_metrics(self, updated_metrics):
        """Implements interface.

        We'll turn the metrics into events for when they changed
        because influxDb doesnt really handle counters, rather it
        just aggregates events. So we'll treat counter changes as events
        with delta values from the prior counter.
        """
        super(InfluxDbMetricsRegistry, self)._do_flush_updated_metrics(updated_metrics)
        payload = []

        recent_gauges = self.__recent_gauges
        self.__recent_gauges = set([])
        for metric in updated_metrics:
            name = metric.name
            label_text = self.__to_label_text(metric)
            ingest = self.__export_func_map[metric.family.family_type]
            ingest(name, label_text, metric, payload)

        remaining_gauges = recent_gauges - self.__recent_gauges
        self.__reiterate_recent_gauges(remaining_gauges, payload)

        if not payload:
            logging.debug("No metrics updated.")
            return

        url = "{prefix}/write?db={db}".format(
            prefix=self.options.influxdb_url, db=self.options.influxdb_database
        )
        payload_text = "\n".join(payload)
        request = Request(url, data=str.encode(payload_text))
        request.get_method = lambda: "POST"
        try:
            urlopen(request)
            logging.debug("Updated %d metrics to %s", len(payload), url)
        except IOError as ioex:
            logging.error("Cannot write metrics to %s:\n%s", url, ioex)

    def __to_label_text(self, metric):
        return ",".join(
            [
                "%s=%s" % (key, value)
                for key, value in metric.labels.items()
                if value != ""
            ]
        )

    def __reiterate_recent_gauges(self, gauges, payload):
        now = datetime.datetime.utcnow()
        keep_if_newer_than = now - datetime.timedelta(
            0, self.options.influxdb_reiterate_gauge_secs
        )

        for gauge in gauges:
            current = gauge.timeseries[-1]
            if gauge.value != 0 or current.utc > keep_if_newer_than:
                # Gauge is still lingering in our reporting
                self.__recent_gauges.add(gauge)

            payload.append(
                self.__to_payload_line(
                    "gauge", gauge.name, self.__to_label_text(gauge), current.value, now
                )
            )

    def __to_payload_line(self, type_name, name, labels, value, utc):
        if labels.endswith(","):
            # This can happen if we have a context but no labels for this occurance
            labels = labels[:-1]

        if labels:
            series = "{name}__{type},{labels}".format(
                name=name, type=type_name, labels=labels
            )
        else:
            series = "{name}__{type}".format(name=name, type=type_name)
        return "{series} value={value} {time}".format(
            series=series, value=value, time=to_timestamp(utc)
        )

    def __export_counter_points(self, name, label_text, metric, payload):
        prev_value = 0
        for entry in metric.mark_as_delta():
            delta_value = entry.value - prev_value
            prev_value = entry.value
            payload.append(
                self.__to_payload_line(
                    "counter", name, label_text, delta_value, entry.utc
                )
            )

    def __export_gauge_points(self, name, label_text, metric, payload):
        self.__recent_gauges.add(metric)
        for entry in metric.mark_as_delta():
            payload.append(
                self.__to_payload_line(
                    "gauge", name, label_text, entry.value, entry.utc
                )
            )

    def __export_timer_points(self, name, label_text, metric, payload):
        prev_count = 0
        prev_total = 0
        for entry in metric.mark_as_delta():
            count = entry.value[0]
            total_secs = entry.value[1]
            delta_count = count - prev_count
            delta_total = total_secs - prev_total
            prev_count = count
            prev_total = total_secs
            payload.append(
                self.__to_payload_line(
                    "count", name, label_text, delta_count, entry.utc
                )
            )
            payload.append(
                self.__to_payload_line(
                    "totalSecs", name, label_text, delta_total, entry.utc
                )
            )
            avg_secs = delta_total / delta_count
            payload.append(
                self.__to_payload_line("AvgSecs", name, label_text, avg_secs, entry.utc)
            )
