# Copyright 2016 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the 'License');
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an 'AS IS' BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Test scenario for Gcp Http(s) Load Balancers.

# Standard python modules.
import copy
import json
import time

# citest modules.
import citest.gcp_testing as gcp
import citest.json_predicate as jp
import citest.service_testing as st
from citest.json_contract import ObservationPredicateFactory
ov_factory = ObservationPredicateFactory()

# Spinnaker modules.
import spinnaker_testing as sk
import spinnaker_testing.gate as gate


SCOPES = [gcp.COMPUTE_READ_WRITE_SCOPE]
GCE_URL_PREFIX = 'https://www.googleapis.com/compute/v1/projects/'

class GoogleHttpLoadBalancerTestScenario(sk.SpinnakerTestScenario):
  '''Defines the tests for L7 Load Balancers.
  '''

  MINIMUM_PROJECT_QUOTA = {
    'INSTANCE_TEMPLATES': 1,
    'BACKEND_SERVICES': 3,
    'URL_MAPS': 1,
    'HEALTH_CHECKS': 1,
    'IN_USE_ADDRESSES': 2,
    'SSL_CERTIFICATES': 2,
    'TARGET_HTTP_PROXIES': 1,
    'TARGET_HTTPS_PROXIES': 1,
    'FORWARDING_RULES': 2
  }

  MINIMUM_REGION_QUOTA = {
      'CPUS': 2,
      'IN_USE_ADDRESSES': 2,
      'INSTANCE_GROUP_MANAGERS': 1,
      'INSTANCES': 2,
  }

  @classmethod
  def new_agent(cls, bindings):
    '''Implements citest.service_testing.AgentTestScenario.new_agent.'''
    agent = gate.new_agent(bindings)
    agent.default_max_wait_secs = 1200
    return agent


  def __init__(self, bindings, agent=None):
    '''Constructor.

    Args:
      bindings: [dict] The data bindings to use to configure the scenario.
      agent: [GateAgent] The agent for invoking the test operations on Gate.
    '''
    super(GoogleHttpLoadBalancerTestScenario, self).__init__(bindings, agent)

    bindings = self.bindings

    self.__lb_detail = 'httplb'
    self.TEST_APP = bindings['TEST_APP']
    self.__lb_name = '{app}-{stack}-{detail}'.format(
        app=bindings['TEST_APP'], stack=bindings['TEST_STACK'],
        detail=self.__lb_detail)
    self.__first_cert = 'first-cert-%s' % (bindings['TEST_APP'])
    self.__proto_hc = {
      'name': 'basic-' + self.TEST_APP,
      'requestPath': '/',
      'port': 80,
      'checkIntervalSec': 2,
      'timeoutSec': 1,
      'healthyThreshold': 3,
      'unhealthyThreshold': 4,
      'healthCheckType': 'HTTP'
    }
    self.__proto_delete = {
      'type': 'deleteLoadBalancer',
      'cloudProvider': 'gce',
      'loadBalancerType': 'HTTP',
      'loadBalancerName': self.__lb_name,
      'credentials': bindings['SPINNAKER_GOOGLE_ACCOUNT'],
      'user': '[anonymous]'
    }
    self.__proto_upsert = {
      'cloudProvider': 'gce',
      'provider': 'gce',
      'stack': bindings['TEST_STACK'],
      'credentials': bindings['SPINNAKER_GOOGLE_ACCOUNT'],
      'region': bindings['TEST_GCE_REGION'],
      'loadBalancerType': 'HTTP',
      'loadBalancerName': self.__lb_name,
      'urlMapName': self.__lb_name,
      'listenersToDelete': [],
      'portRange': '80',
      'defaultService': {
        'name': 'default-' + self.TEST_APP,
        'backends': [],
        'healthCheck': self.__proto_hc,
      },
      'certificate': self.__first_cert,
      'hostRules': [
        {
          'hostPatterns': ['host1.com', 'host2.com'],
          'pathMatcher': {
            'pathRules': [
              {
                'paths': ['/path', '/path2/more'],
                'backendService': {
                  'name': 'bs-' + self.TEST_APP,
                  'backends': [],
                  'healthCheck': self.__proto_hc,
                }
              }
            ],
            'defaultService': {
              'name': 'pm-' + self.TEST_APP,
              'backends': [],
              'healthCheck': self.__proto_hc,
            }
          }
        }
      ],
      'type': 'upsertLoadBalancer',
      'availabilityZones': {bindings['TEST_GCE_REGION']: []},
      'user': '[anonymous]'
    }


  def _get_bs_link(self, bs):
    '''Make a fully-formatted backend service link.
    '''
    return (GCE_URL_PREFIX
            + self.bindings['GOOGLE_PRIMARY_MANAGED_PROJECT_ID']
            + '/global/backendServices/' + bs)


  def _get_hc_link(self, hc):
    '''Make a fully-formatted health check link.
    '''
    return (GCE_URL_PREFIX
            + self.bindings['GOOGLE_PRIMARY_MANAGED_PROJECT_ID']
            + '/global/healthChecks/' + hc)


  def _set_all_hcs(self, upsert, hc):
    '''Set all health checks in upsert to hc.
    '''
    upsert['defaultService']['healthCheck'] = hc
    for host_rule in upsert['hostRules']:
      path_matcher = host_rule['pathMatcher']
      path_matcher['defaultService']['healthCheck'] = hc
      for path_rule in path_matcher['pathRules']:
        path_rule['backendService']['healthCheck'] = hc


  def _add_contract_clauses(self, contract_builder, upsert):
    '''Add the proper predicates to the contract builder for a given
    upsert description.
    '''
    host_rules = upsert['hostRules'] # Host rules will be distinct.
    backend_services = [upsert['defaultService']]
    for host_rule in host_rules:
      path_matcher = host_rule['pathMatcher']
      backend_services.append(path_matcher['defaultService'])
      for path_rule in path_matcher['pathRules']:
        backend_services.append(path_rule['backendService'])
    health_checks = [service['healthCheck'] for service in backend_services]

    hc_clause_builder = (contract_builder
                         .new_clause_builder('Health Checks Created',
                                             retryable_for_secs=30)
                         .list_resource('healthChecks'))
    for hc in health_checks:
      hc_clause_builder.AND(
          ov_factory.value_list_contains(jp.DICT_MATCHES({
              'name': jp.STR_EQ(hc['name']),
              'httpHealthCheck': jp.DICT_MATCHES({
                  'requestPath': jp.STR_EQ(hc['requestPath']),
                  'port': jp.NUM_EQ(hc['port']),
              })
          })))

    bs_clause_builder = (contract_builder.
                         new_clause_builder('Backend Services Created',
                                            retryable_for_secs=30).
                         list_resource('backendServices'))
    for bs in backend_services:
      bs_clause_builder.AND(ov_factory.value_list_contains(jp.DICT_MATCHES({
          'name': jp.STR_EQ(bs['name']),
          'portName': jp.STR_EQ('http'),
          'healthChecks':
              jp.LIST_MATCHES([
                  jp.STR_EQ(self._get_hc_link(bs['healthCheck']['name']))])
        })))

    url_map_clause_builder = (contract_builder
                              .new_clause_builder('Url Map Created',
                                                  retryable_for_secs=30)
                              .list_resource('urlMaps'))
    for hr in host_rules:
      pm = hr['pathMatcher']

      path_rules_spec = [
          jp.DICT_MATCHES({
              'service': jp.STR_EQ(
                  self._get_bs_link(pr['backendService']['name'])),
              'paths':
                  jp.LIST_MATCHES([jp.STR_EQ(path) for path in pr['paths']])
              })
          for pr in pm['pathRules']]

      path_matchers_spec = {
        'defaultService':
            jp.STR_EQ(self._get_bs_link(pm['defaultService']['name'])),
        'pathRules':  jp.LIST_MATCHES(path_rules_spec)
        }

      url_map_clause_builder.AND(
          ov_factory.value_list_contains(jp.DICT_MATCHES({
              'name': jp.STR_EQ(self.__lb_name),
              'defaultService':
                  jp.STR_EQ(self._get_bs_link(upsert['defaultService']['name'])),
              'hostRules/hosts':
                  jp.LIST_MATCHES([jp.STR_SUBSTR(host)
                                   for host in hr['hostPatterns']]),
              'pathMatchers':
                  jp.LIST_MATCHES([jp.DICT_MATCHES(path_matchers_spec)]),
          })))

    port_string = '443-443'
    if upsert['certificate'] == '':
      port_string = '%s-%s' % (upsert['portRange'], upsert['portRange'])

    (contract_builder.new_clause_builder('Forwarding Rule Created',
                                         retryable_for_secs=30)
     .list_resource('globalForwardingRules')
     .EXPECT(ov_factory.value_list_contains(jp.DICT_MATCHES({
          'name': jp.STR_EQ(self.__lb_name),
          'portRange': jp.STR_EQ(port_string)
          }))))

    proxy_clause_builder = contract_builder.new_clause_builder(
      'Target Proxy Created', retryable_for_secs=30)
    self._add_proxy_clause(upsert['certificate'], proxy_clause_builder)


  def _add_proxy_clause(self, certificate, proxy_clause_builder):
    target_proxy_name = '%s-target-%s-proxy'
    if certificate:
      target_proxy_name = target_proxy_name % (self.__lb_name, 'https')
      (proxy_clause_builder.list_resource('targetHttpsProxies')
       .EXPECT(ov_factory.value_list_path_contains(
           'name', jp.STR_EQ(target_proxy_name))))
    else:
      target_proxy_name = target_proxy_name % (self.__lb_name, 'http')
      (proxy_clause_builder.list_resource('targetHttpProxies')
       .EXPECT(ov_factory.value_list_path_contains(
           'name', jp.STR_EQ(target_proxy_name))))


  def upsert_full_load_balancer(self):
    '''Upserts L7 LB with full hostRules, pathMatchers, etc.

    Calls the upsertLoadBalancer operation with a payload, then verifies that
    the expected resources are visible on GCP.
    '''
    hc = copy.deepcopy(self.__proto_hc)
    hc['requestPath'] = '/'
    hc['port'] = 80
    upsert = copy.deepcopy(self.__proto_upsert)
    self._set_all_hcs(upsert, hc)

    payload = self.agent.make_json_payload_from_kwargs(
      job=[upsert],
      description='Upsert L7 Load Balancer: ' + self.__lb_name,
      application=self.TEST_APP
    )

    contract_builder = gcp.GcpContractBuilder(self.gcp_observer)
    self._add_contract_clauses(contract_builder, upsert)

    return st.OperationContract(
      self.new_post_operation(title='upsert full http lb',
                              data=payload, path='tasks'),
      contract=contract_builder.build()
    )


  def upsert_min_load_balancer(self):
    '''Upserts a L7 LB with the minimum description.
    '''
    upsert = copy.deepcopy(self.__proto_upsert)
    upsert['hostRules'] = []
    upsert['certificate'] = '' # Test HTTP upsert, not HTTPS.

    payload = self.agent.make_json_payload_from_kwargs(
      job=[upsert],
      description='Upsert L7 Load Balancer: ' + self.__lb_name,
      application=self.TEST_APP
    )

    contract_builder = gcp.GcpContractBuilder(self.gcp_observer)
    self._add_contract_clauses(contract_builder, upsert)

    return st.OperationContract(
      self.new_post_operation(title='upsert min http lb',
                              data=payload, path='tasks'),
      contract=contract_builder.build()
    )


  def delete_http_load_balancer(self):
    '''Deletes the L7 LB.
    '''
    bindings = self.bindings
    delete = copy.deepcopy(self.__proto_delete)

    payload = self.agent.make_json_payload_from_kwargs(
      job=[delete],
      description='Delete L7 Load Balancer: {0} in {1}'.format(
        self.__lb_name,
        bindings['SPINNAKER_GOOGLE_ACCOUNT'],
      ),
      application=self.TEST_APP
    )
    contract_builder = gcp.GcpContractBuilder(self.gcp_observer)
    (contract_builder.new_clause_builder('Health Check Removed',
                                         retryable_for_secs=30)
     .list_resource('healthChecks')
     .EXPECT(ov_factory.value_list_path_excludes(
         'name', jp.STR_SUBSTR(self.__proto_hc['name'])))
    )
    (contract_builder.new_clause_builder('Url Map Removed',
                                         retryable_for_secs=30)
     .list_resource('urlMaps')
     .EXPECT(ov_factory.value_list_path_excludes(
         'name', jp.STR_SUBSTR(self.__lb_name)))
    )
    (contract_builder.new_clause_builder('Forwarding Rule Removed',
                                         retryable_for_secs=30)
     .list_resource('globalForwardingRules')
     .EXPECT(ov_factory.value_list_path_excludes(
         'name', jp.STR_SUBSTR(self.__lb_name)))
    )

    return st.OperationContract(
      self.new_post_operation(
        title='delete_http_load_balancer', data=payload, path='tasks'),
      contract=contract_builder.build())


  def change_health_check(self):
    '''Changes the health check associated with the LB.
    '''
    upsert = copy.deepcopy(self.__proto_upsert)
    hc = copy.deepcopy(self.__proto_hc)
    hc['requestPath'] = '/changedPath'
    hc['port'] = 8080
    self._set_all_hcs(upsert, hc)

    payload = self.agent.make_json_payload_from_kwargs(
      job=[upsert],
      description='Upsert L7 Load Balancer: ' + self.__lb_name,
      application=self.TEST_APP
    )

    contract_builder = gcp.GcpContractBuilder(self.gcp_observer)
    self._add_contract_clauses(contract_builder, upsert)

    return st.OperationContract(
      self.new_post_operation(title='change health checks',
                              data=payload, path='tasks'),
      contract=contract_builder.build()
    )


  def change_backend_service(self):
    '''Changes the default backend service associated with the LB.
    '''
    hc = copy.deepcopy(self.__proto_hc)
    bs_upsert = copy.deepcopy(self.__proto_upsert)
    hc['name'] = 'updated-' + self.TEST_APP
    hc['requestPath'] = '/changedPath1'
    hc['port'] = 8080

    bs_upsert['defaultService']['healthCheck'] = hc
    payload = self.agent.make_json_payload_from_kwargs(
      job=[bs_upsert],
      description='Upsert L7 Load Balancer: ' + self.__lb_name,
      application=self.TEST_APP
    )

    contract_builder = gcp.GcpContractBuilder(self.gcp_observer)
    self._add_contract_clauses(contract_builder, bs_upsert)

    return st.OperationContract(
      self.new_post_operation(title='change backend services',
                              data=payload, path='tasks'),
      contract=contract_builder.build()
    )


  def add_host_rule(self):
    '''Adds a host rule to the url map.
    '''
    bs_upsert = copy.deepcopy(self.__proto_upsert)
    hr = copy.deepcopy(bs_upsert['hostRules'][0])
    hr['hostPatterns'] = ['added.host1.com', 'added.host2.com']
    hr['pathMatcher']['pathRules'][0]['paths'] = ['/added/path']
    bs_upsert['hostRules'].append(hr)

    payload = self.agent.make_json_payload_from_kwargs(
      job=[bs_upsert],
      description='Upsert L7 Load Balancer: ' + self.__lb_name,
      application=self.TEST_APP
    )

    contract_builder = gcp.GcpContractBuilder(self.gcp_observer)
    self._add_contract_clauses(contract_builder, bs_upsert)

    return st.OperationContract(
      self.new_post_operation(title='add host rule',
                              data=payload, path='tasks'),
      contract=contract_builder.build()
    )


  def update_host_rule(self):
    '''Updates a host rule to the url map.
    '''
    bs_upsert = copy.deepcopy(self.__proto_upsert)
    hr = copy.deepcopy(bs_upsert['hostRules'][0])
    hr['hostPatterns'] = ['updated.host1.com']
    hr['pathMatcher']['pathRules'][0]['paths'] = ['/updated/path']
    bs_upsert['hostRules'].append(hr)

    payload = self.agent.make_json_payload_from_kwargs(
      job=[bs_upsert],
      description='Upsert L7 Load Balancer: ' + self.__lb_name,
      application=self.TEST_APP
    )

    contract_builder = gcp.GcpContractBuilder(self.gcp_observer)
    self._add_contract_clauses(contract_builder, bs_upsert)

    return st.OperationContract(
      self.new_post_operation(title='update host rule',
                              data=payload, path='tasks'),
      contract=contract_builder.build()
    )


  def add_cert(self, certname, title):
    '''Add cert to targetHttpProxy to make it a targetHttpsProxy.
    '''
    bs_upsert = copy.deepcopy(self.__proto_upsert)
    bs_upsert['certificate'] = certname

    payload = self.agent.make_json_payload_from_kwargs(
      job=[bs_upsert],
      description='Upsert L7 Load Balancer: ' + self.__lb_name,
      application=self.TEST_APP
    )

    contract_builder = gcp.GcpContractBuilder(self.gcp_observer)
    self._add_contract_clauses(contract_builder, bs_upsert)

    return st.OperationContract(
      self.new_post_operation(title=title,
                              data=payload, path='tasks'),
      contract=contract_builder.build()
    )


  def add_security_group(self):
    '''Associates a security group with the L7 load balancer.
    '''
    bindings = self.bindings
    sec_group_payload = self.agent.make_json_payload_from_kwargs(
      job=[
        {
          'allowed': [
            {
              'ipProtocol': 'tcp',
              'portRanges': ['80-80']
            },
            {
              'ipProtocol': 'tcp',
              'portRanges': ['8080-8080']
            },
            {
              'ipProtocol': 'tcp',
              'portRanges': ['443-443']
            }
          ],
          'backingData': {'networks': ['default']},
          'cloudProvider': 'gce',
          'application': self.TEST_APP,
          'credentials': bindings['SPINNAKER_GOOGLE_ACCOUNT'],
          'description': '',
          'detail': 'http',
          'ipIngress': [
            {
              'type': 'tcp',
              'startPort': 80,
              'endPort': 80,
            },
            {
              'type': 'tcp',
              'startPort': 8080,
              'endPort': 8080,
            },
            {
              'type': 'tcp',
              'startPort': 443,
              'endPort': 443,
            }
          ],
          'name': self.__lb_name + '-rule',
          'network': 'default',
          'region': 'global',
          'securityGroupName': self.__lb_name + '-rule',
          'sourceRanges': ['0.0.0.0/0'],
          'targetTags': [self.__lb_name + '-tag'],
          'type': 'upsertSecurityGroup',
          'user': '[anonymous]'
        }
      ],
      description='Create a Security Group for L7 operations.',
      application=self.TEST_APP
    )
    builder = gcp.GcpContractBuilder(self.gcp_observer)
    (builder.new_clause_builder('Security Group Created',
                                retryable_for_secs=30)
     .list_resource('firewalls')
     .EXPECT(ov_factory.value_list_path_contains(
         'name', jp.STR_SUBSTR(self.__lb_name + '-rule'))))

    return st.OperationContract(
      self.new_post_operation(title='create security group',
                              data=sec_group_payload, path='tasks'),
      contract=builder.build()
    )

  def delete_security_group(self):
    '''Deletes a security group.
    '''
    bindings = self.bindings
    sec_group_payload = self.agent.make_json_payload_from_kwargs(
      job=[
        {
          'cloudProvider': 'gce',
          'credentials': bindings['SPINNAKER_GOOGLE_ACCOUNT'],
          'regions': ['global'],
          'securityGroupName': self.__lb_name + '-rule',
          'type': 'deleteSecurityGroup',
          'user': '[anonymous]'
        }
      ],
      description='Delete a Security Group.',
      application=self.TEST_APP
    )

    builder = gcp.GcpContractBuilder(self.gcp_observer)
    (builder.new_clause_builder('Security Group Deleted',
                                retryable_for_secs=30)
     .list_resource('firewalls')
     .EXPECT(ov_factory.value_list_path_excludes(
         'name', jp.STR_SUBSTR(self.__lb_name + '-rule'))))

    return st.OperationContract(
      self.new_post_operation(title='delete security group',
                              data=sec_group_payload, path='tasks'),
      contract=builder.build()
    )

  def add_server_group(self):
    '''Adds a server group to the L7 LB.
    '''
    time.sleep(60) # Wait for the L7 LB to be ready.
    bindings = self.bindings
    group_name = '{app}-{stack}-v000'.format(app=self.TEST_APP,
                                             stack=bindings['TEST_STACK'])
    policy = {
      'balancingMode': 'UTILIZATION',
      'namedPorts': [{'name': 'http', 'port': 80}],
      'maxUtilization': 0.8,
      'capacityScaler': 0.8
    }

    payload = self.agent.make_json_payload_from_kwargs(
      job=[{
        'cloudProvider': 'gce',
        'application': self.TEST_APP,
        'credentials': bindings['SPINNAKER_GOOGLE_ACCOUNT'],
        'strategy':'',
        'capacity': {'min':1, 'max':1, 'desired':1},
        'targetSize': 1,
        'image': bindings['TEST_GCE_IMAGE_NAME'],
        'zone': bindings['TEST_GCE_ZONE'],
        'stack': bindings['TEST_STACK'],
        'instanceType': 'f1-micro',
        'type': 'createServerGroup',
        'tags': [self.__lb_name + '-tag'],
        'loadBalancers': [self.__lb_name],
        'backendServices': {self.__lb_name: ['bs-' + self.TEST_APP]},
        'disableTraffic': False,
        'loadBalancingPolicy': {
          'balancingMode': 'UTILIZATION',
          'namedPorts': [{'name': 'http', 'port': 80}],
          'maxUtilization': 0.8,
          'capacityScaler': 0.8
        },
        'availabilityZones': {
          bindings['TEST_GCE_REGION']: [bindings['TEST_GCE_ZONE']]
        },
        'instanceMetadata': {
          'global-load-balancer-names': self.__lb_name,
          'backend-service-names': 'bs-' + self.TEST_APP,
          'load-balancing-policy': json.dumps(policy)
        },
        'account': bindings['SPINNAKER_GOOGLE_ACCOUNT'],
        'authScopes': ['compute'],
        'user': '[anonymous]'
      }],
      description='Create Server Group in ' + group_name,
      application=self.TEST_APP
    )

    builder = gcp.GcpContractBuilder(self.gcp_observer)
    (builder.new_clause_builder('Managed Instance Group Added',
                                retryable_for_secs=30)
     .inspect_resource('instanceGroupManagers', group_name)
     .EXPECT(ov_factory.value_list_path_contains('targetSize', jp.NUM_EQ(1)))
    )

    return st.OperationContract(
      self.new_post_operation(title='create server group',
                              data=payload, path='tasks'),
      contract=builder.build()
    )


  def delete_server_group(self):
    """Creates OperationContract for deleteServerGroup.

    To verify the operation, we just check that the GCP managed instance group
    is no longer visible on GCP (or is in the process of terminating).
    """
    bindings = self.bindings
    group_name = '{app}-{stack}-v000'.format(
        app=self.TEST_APP, stack=bindings['TEST_STACK'])

    payload = self.agent.make_json_payload_from_kwargs(
      job=[{
        'cloudProvider': 'gce',
        'serverGroupName': group_name,
        'region': bindings['TEST_GCE_REGION'],
        'zone': bindings['TEST_GCE_ZONE'],
        'type': 'destroyServerGroup',
        'regions': [bindings['TEST_GCE_REGION']],
        'zones': [bindings['TEST_GCE_ZONE']],
        'credentials': bindings['SPINNAKER_GOOGLE_ACCOUNT'],
        'user': '[anonymous]'
      }],
      application=self.TEST_APP,
      description='DestroyServerGroup: ' + group_name
    )

    builder = gcp.GcpContractBuilder(self.gcp_observer)
    (builder.new_clause_builder('Managed Instance Group Removed')
     .inspect_resource('instanceGroupManagers', group_name)
     .EXPECT(
         ov_factory.error_list_contains(gcp.HttpErrorPredicate(http_code=404)))
     .OR(ov_factory.value_list_path_contains('targetSize', jp.NUM_EQ(0))))

    (builder.new_clause_builder('Instances Are Removed',
                                retryable_for_secs=30)
     .list_resource('instances')
     .EXPECT(ov_factory.value_list_path_excludes(
         'name', jp.STR_SUBSTR(group_name))))

    return st.OperationContract(
      self.new_post_operation(
        title='delete server group', data=payload, path='tasks'),
      contract=builder.build()
    )
