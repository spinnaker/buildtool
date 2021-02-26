#!/bin/bash
#
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

set -e
set -x
set -o pipefail

# Import some functions from other scripts.
source $(dirname $0)/build_google_image_functions.sh


HAL_DAEMON_ENDPOINT=http://localhost:8064

function show_usage() {
    fix_defaults

cat <<EOF
Usage:  $0 [options]

   --artifact
       [$ARTIFACT]
       Build a component VM image for this artifact.

   --account ACCOUNT
       [$ACCOUNT]
       Use this gcloud account to build the image.

   --hal_daemon_endpoint ENDPOINT
       [$HAL_DAEMON_ENDPOINT]
       The endpoint for the halyard daemon when determining the build version

   --image_project IMAGE_PROJECT
      [$IMAGE_PROJECT]
      The project for the SOURCE_IMAGE or BASE_IMAGE. The default is the
      PROJECT.

   --install_script INSTALL_SCRIPT
       [$INSTALL_SCRIPT]
       The path or URL to the install script to use.

   --extra_install_script_args EXTRA_INSTALL_SCRIPT_ARGS
     [$EXTRA_INSTALL_SCRIPT_ARGS]
     Additional arguments to pass to the INSTALL_SCRIPT

   --no_update_os
       Do not force an upgrade-dist of the base OS.

   --build_project BUILD_PROJECT
       [$BUILD_PROJECT]
       Build the images in the BUILD_PROJECT id.

   --publish_project PUBLISH_PROJECT
       [$PUBLISH_PROJECT]
       Publish the images in the PUBLISH_PROJECT id.

   --publish_script PUBLISH_SCRIPT
       [$PUBLISH_SCRIPT]
       Script to use to publish the images.

   --version VERSION
       [$VERSION]
       The exact Spinnaker version we are baking images for.

   --zone ZONE
       [$ZONE]
       Zone to use when building the image. The final image is global.

   --spinnaker_dev_github_owner
       [$SPINNAKER_DEV_GITHUB_OWNER]
       The github spinnaker repository owner to get install scripts from
       on the provisioned machines building the image. This is only used
       during the build.

   --spinnaker_dev_github_branch
       [$SPINNAKER_DEV_GITHUB_BRANCH]
       The github spinnaker repository branch for SPINNAKER_DEV_GITHUB_OWNER
EOF
}


function process_args() {
  while [[ $# > 0 ]]; do
    local key="$1"
    shift

    case $key in
        --help)
            show_usage
            exit
            ;;
        --artifact)
            ARTIFACT=$1
            shift
            ;;
        --account)
            ACCOUNT=$1
            shift
            ;;
        --hal_daemon_endpoint)
            HAL_DAEMON_ENDPOINT=$1
            shift
            ;;
        --image_project)
            IMAGE_PROJECT=$1
            shift
            ;;
        --install_script)
            INSTALL_SCRIPT=$1
            shift
            ;;
        --extra_install_script_args)
            EXTRA_INSTALL_SCRIPT_ARGS="$1"
            shift
            ;;
        --no_update_os)
            UPDATE_OS=false
            ;;
        --build_project)
            BUILD_PROJECT=$1
            shift
            ;;
        --publish_project)
            PUBLISH_PROJECT=$1
            shift
            ;;
        --publish_script)
            PUBLISH_SCRIPT=$1
            shift
            ;;
        --version)
            VERSION=$1
            shift
            ;;
        --zone)
            ZONE=$1
            shift
            ;;
        --spinnaker_dev_github_owner)
            SPINNAKER_DEV_GITHUB_OWNER=$1
            shift
            ;;
        --spinnaker_dev_github_branch)
            SPINNAKER_DEV_GITHUB_BRANCH=$1
            shift
            ;;

        *)
          show_usage
          >&2 echo "Unrecognized argument '$key'."
          exit -1
    esac
  done
}


function create_component_prototype_disk() {
  local install_script_path
  local component=$1
  local version=$2

  install_script_path=$(basename $INSTALL_SCRIPT)
  local ssh_key=$(cat ${SSH_KEY_FILE}.pub)
  if [[ $ssh_key == ssh-rsa* ]]; then
      ssh_key="$LOGNAME:$ssh_key"
  fi

  echo "`date`: Creating prototype instance '$BUILD_INSTANCE' from $BASE_IMAGE."
  gcloud compute instances create $BUILD_INSTANCE \
      --project $BUILD_PROJECT \
      --account $ACCOUNT \
      --zone $ZONE \
      --machine-type n1-standard-1 \
      --boot-disk-type pd-ssd \
      --image $BASE_IMAGE  \
      --image-project $IMAGE_PROJECT \
      --metadata block-project-ssh-keys=TRUE,startup-script="apt-get update && apt-get install -y git && git clone https://github.com/$SPINNAKER_DEV_GITHUB_OWNER/spinnaker.git -b $SPINNAKER_DEV_GITHUB_BRANCH",ssh-keys="$ssh_key"

  trap cleanup_instances_on_error EXIT

  PROTOTYPE_INSTANCE=$BUILD_INSTANCE

  # This second instance will be used later to clean the image
  # we dont need it yet, but will spin it up now to have it ready.
  echo "`date` Warming up '$CLEANER_INSTANCE' for later"
  (gcloud compute instances create ${CLEANER_INSTANCE} \
      --project $BUILD_PROJECT \
      --account $ACCOUNT \
      --zone $ZONE \
      --machine-type n1-standard-1 \
      --image $BASE_IMAGE \
      --metadata startup-script="apt-get update && apt-get install -y git && git clone https://github.com/$SPINNAKER_DEV_GITHUB_OWNER/spinnaker.git -b $SPINNAKER_DEV_GITHUB_BRANCH",ssh-keys="$ssh_key" \
      --image-project $IMAGE_PROJECT >& /dev/null&)

  args="--component $component --version $version"
  command_path="/buildtool/dev/$(basename $INSTALL_SCRIPT)"
  command="sudo bash $command_path ${args}"
  command="$command ${EXTRA_INSTALL_SCRIPT_ARGS}"

  echo "`date`: Waiting for instance to be ready..."
  sleep 30 # initial seed time
  for i in {1..15}; do
    # Wait for both ssh to be ready and startup script to clone the repo
    if gcloud compute ssh $BUILD_INSTANCE \
      --project $BUILD_PROJECT \
      --account $ACCOUNT \
      --zone $ZONE \
      --ssh-key-file $SSH_KEY_FILE \
      --command="if [[ -f $command_path ]]; then exit 0; else exit -1; fi"
    then
        break
    else
        echo "`date`: Not yet, wait and try again."
        sleep 10
    fi
  done

  echo "`date`: Installing $component and spinnaker-monitoring onto '$BUILD_INSTANCE'"
  gcloud compute ssh $BUILD_INSTANCE \
    --project $BUILD_PROJECT \
    --account $ACCOUNT \
    --zone $ZONE \
    --ssh-key-file $SSH_KEY_FILE \
    --command="$command"

  if [[ "$UPDATE_OS" == "true" ]]; then
    echo "`date`: Updating distribution on '$BUILD_INSTANCE'"
    gcloud compute ssh $BUILD_INSTANCE \
      --project $BUILD_PROJECT \
      --account $ACCOUNT \
      --zone $ZONE \
      --ssh-key-file $SSH_KEY_FILE \
      --command="sudo DEBIAN_FRONTEND=noninteractive apt-get -y dist-upgrade && sudo apt-get autoremove -y"
  fi

  echo "`date`: Setting auto-delete behavior '$BUILD_INSTANCE' disk to false"
  gcloud compute instances set-disk-auto-delete $BUILD_INSTANCE \
    --project $BUILD_PROJECT \
    --account $ACCOUNT \
    --zone $ZONE \
    --no-auto-delete \
    --disk $BUILD_INSTANCE

  # This will be on success too
  trap delete_prototype_disk EXIT

  # Just the builder instance, not the cleanup instance
  delete_build_instance
}


function extract_clean_component_disk() {
  local prototype_disk="$1"
  local worker_instance="$2"
  local output_file="$3"

  if [[ $output_file != "" ]]; then
    if [[ "$output_file" != gs://*.tar.gz ]]; then
      echo "$output_file is not a gs:// path to a tar.gz file"
      exit -1
    fi
  fi

  gcloud compute instances describe ${worker_instance} \
      --project $PROJECT \
      --account $ACCOUNT \
      --zone $ZONE > /dev/null

  if [ $? -ne 0 ]; then
    >&2 echo "`date`: ERROR - ${worker_instance} was not created."
    >&2 echo "Check if you have enough quota (CPU/IP) to create all instances."
    exit 1
  fi

  echo "`date`: Preparing '$worker_instance'"
  gcloud compute instances attach-disk ${worker_instance} \
      --project $PROJECT \
      --account $ACCOUNT \
      --zone $ZONE \
      --disk $prototype_disk \
      --device-name spinnaker

  echo "`date`: Cleaning in '$worker_instance'"
  gcloud compute ssh ${worker_instance} \
      --project $PROJECT \
      --account $ACCOUNT \
      --zone $ZONE \
      --ssh-key-file $SSH_KEY_FILE \
      --command="sudo bash /buildtool/dev/clean_google_image.sh spinnaker"

  if [[ $output_file != "" ]]; then
    echo "`date`: Extracting disk as tar file '$output_file.'"
    gcloud compute ssh ${worker_instance} \
        --project $PROJECT \
        --account $ACCOUNT \
        --zone $ZONE \
        --ssh-key-file $SSH_KEY_FILE \
        --command="sudo bash /buildtool/dev/extract_disk_to_gcs.sh spinnaker $output_file"
  fi

  gcloud compute instances detach-disk ${worker_instance} \
      --project $PROJECT \
      --account $ACCOUNT \
      --zone $ZONE \
      --disk $prototype_disk
  echo "`date`: Finished cleaning disk '$prototype_disk'."
}


function create_component_image() {
  local artifact=$1
  local service=$2
  ARTIFACT_VERSION="$(hal version bom $VERSION --artifact-name ${artifact} --quiet --color false --daemon-endpoint $HAL_DAEMON_ENDPOINT)"
  # Target image is named spinnaker-${artifact}-${artifact-version} with dashes replacing dots.
  TARGET_IMAGE="$(echo spinnaker-${artifact}-${ARTIFACT_VERSION} | sed 's/[\.:]/\-/g')"
  echo $TARGET_IMAGE
  CLEANER_INSTANCE="clean-${TARGET_IMAGE}"
  BUILD_INSTANCE="build-${TARGET_IMAGE}"

  create_component_prototype_disk $service $VERSION
  extract_clean_component_disk "$BUILD_INSTANCE" "$CLEANER_INSTANCE"
  image_from_prototype_disk "$TARGET_IMAGE" "$BUILD_INSTANCE"

  trap - EXIT

  delete_prototype_disk


  if [[ "$PUBLISH_PROJECT" == "$BUILD_PROJECT" ]]; then
    return
  fi

  # Set $PROJECT to the publish project so we can clear the target
  # image and disk if it exists.
  PROJECT=$PUBLISH_PROJECT
  delete_disk_if_exists $TARGET_IMAGE
  delete_image_if_exists $TARGET_IMAGE
  bash $PUBLISH_SCRIPT \
    --zone $ZONE \
    --service_account $ACCOUNT \
    --original_image $TARGET_IMAGE \
    --original_project $BUILD_PROJECT \
    --publish_image $TARGET_IMAGE \
    --publish_project $PUBLISH_PROJECT

  # Clear the image and disk from the build project after the copy.
  PROJECT=$BUILD_PROJECT
  delete_disk_if_exists $TARGET_IMAGE
  delete_image_if_exists $TARGET_IMAGE
}


function fix_defaults() {
  # No source image, so assume a base image (to install from).
  if [[ "$SOURCE_IMAGE" == "" ]]; then
    local image_entry=$(gcloud compute images list 2>&1 \
                        | grep $BASE_IMAGE_OR_FAMILY | head -1)

    BASE_IMAGE=$(echo "$image_entry" | sed "s/\([^ ]*\) .*/\1/")

    # If this was a family, convert it to a particular image for
    # argument consistency
    if [[ "$IMAGE_PROJECT" == "" ]]; then
      IMAGE_PROJECT=$(echo "$image_entry" | sed "s/[^ ]* *\([^ ]*\)* .*/\1/")
    fi
  fi
}


# map of artifact -> service
# artifact is an unconfigured installable package/binary
# service is a configured artifact
declare -A COMPONENTS=( ['clouddriver']='clouddriver' \
  ['deck']='deck' \
  ['echo']='echo' \
  ['fiat']='fiat' \
  ['front50']='front50' \
  ['gate']='gate' \
  ['igor']='igor' \
  ['kayenta']='kayenta' \
  ['keel']='keel' \
  ['orca']='orca' \
  ['rosco']='rosco' \
  ['consul']='consul-server' \
  ['vault']='vault-server' \
  ['redis']='redis')

TIME_DECORATOR=$(date +%Y%m%d%H%M%S)
ZONE=us-central1-f
SPINNAKER_DEV_GITHUB_OWNER=spinnaker
SPINNAKER_DEV_GITHUB_BRANCH=master
BASE_IMAGE_OR_FAMILY=ubuntu-1404-lts
BASE_IMAGE=$BASE_IMAGE_OR_FAMILY

process_args "$@"

PROJECT=$BUILD_PROJECT

fix_defaults
ensure_empty_ssh_key

service=${COMPONENTS[$ARTIFACT]}
echo "Creating component image for $service with artifact $ARTIFACT"
create_component_image $ARTIFACT $service

echo "`date`: DONE"
