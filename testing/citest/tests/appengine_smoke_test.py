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

"""
Integration test for App Engine.

Primarily tests the deploy operation and upsert load balancer pipeline stage,
which are relatively complex and not well covered by unit tests.

Sample Usage:
    Before running this test, verify that the App Engine application
    in your GCP project has a default service. If it does not, deploy
    any App Engine version to your application that will use the default service.

    Assuming you have created $PASSPHRASE_FILE (which you should chmod 400)
    and $CITEST_ROOT points to the root directory of the citest library.
    The passphrase file can be omited if you run ssh-agent and add
    .ssh/compute_google_engine.

  PYTHONPATH=$CITEST_ROOT \
    python buildtool/testing/citest/tests/appengine_smoke_test.py \
    --gce_ssh_passphrase_file=$PASSPHRASE_FILE \
    --gce_project=$PROJECT \
    --gce_zone=$ZONE \
    --gce_instance=$INSTANCE
or
  PYTHONPATH=$CITEST_ROOT \
    python buildtool/testing/citest/tests/appengine_smoke_test.py \
    --native_hostname=host-running-smoke-test
"""

import logging
import os
import shutil
import subprocess
import sys
import tempfile

import citest.gcp_testing as gcp
import citest.json_contract as jc
import citest.json_predicate as jp
import citest.service_testing as st
ov_factory = jc.ObservationPredicateFactory()

import spinnaker_testing as sk
import spinnaker_testing.gate as gate
import spinnaker_testing.frigga as frigga
import citest.base


class AppengineSmokeTestScenario(sk.SpinnakerTestScenario):
  """Defines the scenario for the integration test.

  We're going to:
    Create a Spinnaker Application
    Create a Spinnaker Server Group (implicitly creates a Load Balancer)
    Create a Pipeline with the following stages
      - Deploy
      - Upsert Load Balancer
    Delete Load Balancer (implicitly destroys the Server Groups
    created within this test)
    Delete Application
  """
  @classmethod
  def new_agent(cls, bindings):
    return gate.new_agent(bindings)

  @classmethod
  def initArgumentParser(cls, parser, defaults=None):
    """Initialize command line argument parser."""
    super(AppengineSmokeTestScenario, cls).initArgumentParser(
          parser, defaults=defaults)
    parser.add_argument(
        '--test_gcs_bucket', default=None,
        help='URL to use for testing appengine deployment from a bucket.'
             ' The test will write into this bucket'
             ' then deploy what it writes.')

    parser.add_argument(
        '--test_storage_account_name', default=None,
        help='Storage account when testing GCS buckets.'
        ' If not specified, use the application default credentials.')

    parser.add_argument(
        '--test_appengine_region', default='us-central',
        help='Region to use for AppEngine tests.')

    parser.add_argument('--git_repo_url', default=None,
                        help='URL of a GIT source code repository used by Spinnaker to deploy to App Engine.')
    parser.add_argument('--branch', default='master',
                        help='Git branch to be used when deploying from source code repository.')
    parser.add_argument('--app_directory_root', default=None,
                        help='Path from the root of source code repository to the application directory.')

  def __init__(self, bindings, agent=None):
    super(AppengineSmokeTestScenario, self).__init__(bindings, agent)

    if not bindings['GIT_REPO_URL']:
      raise ValueError('Must supply value for --git_repo_url')

    if not bindings['APP_DIRECTORY_ROOT']:
      raise ValueError('Must supply value for --app_directory_root')

    self.TEST_APP = bindings['TEST_APP']
    self.TEST_STACK = bindings['TEST_STACK']
    self.__path = 'applications/%s/tasks' % self.TEST_APP
    self.__gcp_project = bindings['APPENGINE_PRIMARY_MANAGED_PROJECT_ID']
    self.__cluster_name = frigga.Naming.cluster(self.TEST_APP, self.TEST_STACK)
    self.__server_group_name = frigga.Naming.server_group(self.TEST_APP, self.TEST_STACK)
    self.__lb_name = self.__cluster_name

    # Python is clearly hard-coded as the runtime here, but we're just asking App Engine to be a static file server.
    self.__app_yaml = ('\n'.join(['runtime: python27',
                                  'api_version: 1',
                                  'threadsafe: true',
                                  'service: {service}',
                                  'handlers:',
                                  ' - url: /.*',
                                  '   static_dir: .']).format(service=self.__lb_name))

    self.__app_directory_root = bindings['APP_DIRECTORY_ROOT']
    self.__branch = bindings['BRANCH']
    self.pipeline_id = None

    try:
      repo_path = self.__clone_app_repo()
      appengine_dir = self.bindings['APP_DIRECTORY_ROOT']
      repo_appengine_path = os.path.join(repo_path, appengine_dir)

      self.__prepare_app_default_version(repo_appengine_path)

      test_bucket = bindings['TEST_GCS_BUCKET']
      if test_bucket:
        self.__prepare_bucket(test_bucket, repo_appengine_path)
        self.__test_repository_url = 'gs://' + test_bucket
      else:
        self.__test_repository_url = bindings['GIT_REPO_URL']
    finally:
      shutil.rmtree(repo_path)

  def __clone_app_repo(self):
    temp = tempfile.mkdtemp()

    git_repo = self.bindings['GIT_REPO_URL']
    branch = self.bindings['BRANCH']

    command = 'git clone {repo} -b {branch} {dir}'.format(
        repo=git_repo, branch=branch, dir=temp)
    logging.info('Fetching %s', git_repo)
    subprocess.Popen(command, stderr=sys.stderr, shell=True).wait()

    return temp

  def __prepare_bucket(self, bucket, repo_appengine_path):
    root = self.bindings['APP_DIRECTORY_ROOT']
    gcs_path = 'gs://{bucket}/{root}'.format(
        bucket=self.bindings['TEST_GCS_BUCKET'], root=root)

    command = 'gsutil -m rsync {local} {gcs}'.format(
        local=repo_appengine_path, gcs=gcs_path)
    logging.info('Preparing %s', gcs_path)
    subprocess.Popen(command, stderr=sys.stderr, shell=True).wait()

  def __prepare_app_default_version(self, repo_appengine_path):
    if not self.__has_default_version():
      deployable_path = os.path.join(repo_appengine_path, 'app.yaml')
      command = 'gcloud app deploy {deployable} --project={project} --quiet'.format(
          project=self.__gcp_project, deployable=deployable_path)
      logging.info('Deploying AppEngine app with default version')
      subprocess.Popen(command, stderr=sys.stderr, shell=True).wait()

  def __has_default_version(self):
    command = 'gcloud app services list --project={project}'.format(
        project=self.__gcp_project)
    out, err = (subprocess.Popen(command,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 shell=True)
                .communicate())
    logging.debug(
        'Checking if project has default app version: {command} returned: {out}'
        .format(command=command, out=out))

    # Expect an output similar to:
    # SERVICE        NUM_VERSIONS
    # default        1
    # other_version  1
    return '\ndefault ' in out.decode(encoding='utf-8')

  def create_app(self):
    # Not testing create_app, since the operation is well tested elsewhere.
    # Retryable to handle platform flakiness.
    contract = jc.Contract()
    return st.OperationContract(
      self.agent.make_create_app_operation(
        bindings=self.bindings,
        application=self.TEST_APP,
        account_name=self.bindings['SPINNAKER_APPENGINE_ACCOUNT'],
        cloud_providers="appengine"),
      contract=contract)

  def delete_app(self):
    # Not testing delete_app, since the operation is well tested elsewhere.
    # Retryable to handle platform flakiness.
    contract = jc.Contract()
    return st.OperationContract(
        self.agent.make_delete_app_operation(
            application=self.TEST_APP,
            account_name=self.bindings['SPINNAKER_APPENGINE_ACCOUNT']),
        contract=contract)

  def create_server_group(self):
    group_name = frigga.Naming.server_group(
        app=self.TEST_APP,
        stack=self.bindings['TEST_STACK'],
        version='v000')
    job_spec = {
        'application': self.TEST_APP,
        'stack': self.TEST_STACK,
        'credentials': self.bindings['SPINNAKER_APPENGINE_ACCOUNT'],
        'repositoryUrl': self.__test_repository_url,
        'applicationDirectoryRoot': self.__app_directory_root,
        'configFiles': [self.__app_yaml],
        'type': 'createServerGroup',
        'cloudProvider': 'appengine',
        'region': self.bindings['TEST_APPENGINE_REGION']
      }
    storageAccountName = self.bindings.get('TEST_STORAGE_ACCOUNT_NAME')
    if storageAccountName is not None:
      job_spec['storageAccountName'] = storageAccountName

    if not self.__test_repository_url.startswith('gs://'):
      job_spec.update({
          'gitCredentialType': 'NONE',
          'branch': self.__branch
      })

    payload = self.agent.make_json_payload_from_kwargs(job=[job_spec],
      description='Create Server Group in ' + group_name,
      application=self.TEST_APP)

    builder = gcp.GcpContractBuilder(self.appengine_observer)
    (builder.new_clause_builder('Version Added', retryable_for_secs=60)
      .inspect_resource('apps.services.versions',
                        group_name,
                        appsId=self.__gcp_project,
                        servicesId=self.__lb_name)
     .EXPECT(ov_factory.value_list_path_contains(
         'servingStatus', jp.STR_EQ('SERVING'))))

    return st.OperationContract(
        self.new_post_operation(
            title='create_server_group', data=payload, path='tasks'),
        contract=builder.build())

  def make_deploy_stage(self):
    cluster_spec = {
        'account': self.bindings['SPINNAKER_APPENGINE_ACCOUNT'],
        'applicationDirectoryRoot': self.__app_directory_root,
        'configFiles': [self.__app_yaml],
        'application': self.TEST_APP,
        'cloudProvider': 'appengine',
        'provider': 'appengine',
        'region': self.bindings['TEST_APPENGINE_REGION'],
        'repositoryUrl': self.__test_repository_url,
        'stack': self.TEST_STACK
    }
    if not self.__test_repository_url.startswith('gs://'):
      cluster_spec.update({
          'gitCredentialType': 'NONE',
          'branch': self.__branch
      })

    result = {
      'clusters': [cluster_spec],
      'name': 'Deploy',
      'refId': '1',
      'requisiteStageRefIds': [],
      'type': 'deploy'
    }
    return result

  def make_upsert_load_balancer_stage(self):
    result = {
        'cloudProvider': 'appengine',
        'loadBalancers': [
        {
            'cloudProvider': 'appengine',
            'credentials': self.bindings['SPINNAKER_APPENGINE_ACCOUNT'],
            'loadBalancerName': self.__lb_name,
            'migrateTraffic': False,
            'name': self.__lb_name,
            'region': self.bindings['TEST_APPENGINE_REGION'],
            'splitDescription': {
                'allocationDescriptions': [
                {
                    'allocation': 0.1,
                    'cluster': self.__cluster_name,
                    'locatorType': 'targetCoordinate',
                   'target': 'current_asg_dynamic'
                },
                {
                    'allocation': 0.9,
                    'cluster': self.__cluster_name,
                    'locatorType': 'targetCoordinate',
                    'target': 'ancestor_asg_dynamic'
                }
                ],
                'shardBy': 'IP'
            }
        }
      ],
      'name': 'Edit Load Balancer',
      'refId': '2',
      'requisiteStageRefIds': ['1'],
      'type': 'upsertAppEngineLoadBalancers'
    }
    return result

  def create_deploy_upsert_load_balancer_pipeline(self):
    name = 'promoteServerGroupPipeline'
    self.pipeline_id = name
    deploy_stage = self.make_deploy_stage()
    upsert_load_balancer_stage = self.make_upsert_load_balancer_stage()

    pipeline_spec = dict(
        name=name,
        stages=[deploy_stage, upsert_load_balancer_stage],
        triggers=[],
        application=self.TEST_APP,
        stageCounter=2,
        parallel=True,
        limitConcurrent=True,
        appConfig={},
        index=0
    )

    payload = self.agent.make_json_payload_from_kwargs(**pipeline_spec)

    builder = st.HttpContractBuilder(self.agent)
    (builder.new_clause_builder('Has Pipeline',
                                retryable_for_secs=5)
     .get_url_path('applications/{0}/pipelineConfigs'.format(self.TEST_APP))
     .contains_path_value(None, pipeline_spec))

    return st.OperationContract(
      self.new_post_operation(
        title='create_deploy_upsert_load_balancer_pipeline', data=payload, path='pipelines',
        status_class=st.SynchronousHttpOperationStatus),
      contract=builder.build())

  def run_deploy_upsert_load_balancer_pipeline(self):
    url_path = 'pipelines/{0}/{1}'.format(self.TEST_APP, self.pipeline_id)

    previous_group_name = frigga.Naming.server_group(
        app=self.TEST_APP,
        stack=self.TEST_STACK,
        version='v000')

    deployed_group_name = frigga.Naming.server_group(
        app=self.TEST_APP,
        stack=self.TEST_STACK,
        version='v001')

    payload = self.agent.make_json_payload_from_kwargs(
        type='manual',
        user='[anonymous]')

    builder = gcp.GcpContractBuilder(self.appengine_observer)
    (builder.new_clause_builder('Service Modified', retryable_for_secs=60)
     .inspect_resource('apps.services',
                       self.__lb_name,
                       appsId=self.__gcp_project)
     .EXPECT(
         ov_factory.value_list_path_contains(
             jp.build_path('split', 'allocations'),
             jp.DICT_MATCHES({previous_group_name: jp.NUM_EQ(0.9),
                              deployed_group_name: jp.NUM_EQ(0.1)}))))

    return st.OperationContract(
        self.new_post_operation(
            title='run_deploy_upsert_load_balancer_pipeline',
            data=payload, path=url_path),
        builder.build())

  def delete_load_balancer(self):
    bindings = self.bindings
    payload = self.agent.make_json_payload_from_kwargs(
        job=[{
            'type': 'deleteLoadBalancer',
            'cloudProvider': 'appengine',
            'loadBalancerName': self.__lb_name,
            'account': bindings['SPINNAKER_APPENGINE_ACCOUNT'],
            'credentials': bindings['SPINNAKER_APPENGINE_ACCOUNT'],
            'user': '[anonymous]'
        }],
        description='Delete Load Balancer: {0} in {1}'.format(
            self.__lb_name,
            bindings['SPINNAKER_APPENGINE_ACCOUNT']),
        application=self.TEST_APP)

    builder = gcp.GcpContractBuilder(self.appengine_observer)
    (builder.new_clause_builder('Service Deleted', retryable_for_secs=60)
     .inspect_resource('apps.services',
                       self.__lb_name,
                       appsId=self.__gcp_project)
     .EXPECT(
         ov_factory.error_list_contains(gcp.HttpErrorPredicate(http_code=404))))

    return st.OperationContract(
        self.new_post_operation(
            title='delete_load_balancer', data=payload, path='tasks'),
        contract=builder.build())

class AppengineSmokeTest(st.AgentTestCase):

  @property
  def scenario(self):
    return citest.base.TestRunner.global_runner().get_shared_data(AppengineSmokeTestScenario)

  def test_a_create_app(self):
    self.run_test_case(self.scenario.create_app(),
                       retry_interval_secs=8, max_retries=8)

  def test_b_create_server_group(self):
    self.run_test_case(self.scenario.create_server_group())

  def test_c_create_pipeline(self):
    self.run_test_case(self.scenario.create_deploy_upsert_load_balancer_pipeline())

  def test_d_run_pipeline(self):
    self.run_test_case(self.scenario.run_deploy_upsert_load_balancer_pipeline())

  def test_y_delete_load_balancer(self):
    self.run_test_case(self.scenario.delete_load_balancer(),
                       retry_interval_secs=8, max_retries=8)

  def test_z_delete_app(self):
    self.run_test_case(self.scenario.delete_app(),
                       retry_interval_secs=8, max_retries=8)


def main():
  defaults = {
      'TEST_STACK': AppengineSmokeTestScenario.DEFAULT_TEST_ID,
      'TEST_APP': 'gaesmoketest' + AppengineSmokeTestScenario.DEFAULT_TEST_ID,
  }

  return citest.base.TestRunner.main(
      parser_inits=[AppengineSmokeTestScenario.initArgumentParser],
      default_binding_overrides=defaults,
      test_case_list=[AppengineSmokeTest])


if __name__ == '__main__':
  sys.exit(main())
