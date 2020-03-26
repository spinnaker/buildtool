#!/bin/bash

# Run this script from the root directory of a spinnaker microservice repository
# to build a container for that service. As an argument, pass the OS Dockerfile
# that you would like to build (e.g. Dockerfile.slim or Dockerfile.ubuntu)

if [[ $1 == "" || ! $(basename $1) =~ ^Dockerfile\. ]]; then
  echo "You must pass a Dockerfile as an argument (e.g. Dockerfile.slim)"
  exit 1
fi

if [[ ! -f Dockerfile.compile ]]; then
  echo "This must be run from within the root directory of a spinnaker "
  echo "microservice repository."
  exit 1
fi

set -e

./gradlew clean
docker build -f Dockerfile.compile -t compile .
docker run \
  --user $(id -u):$(id -g) \
  --mount type=bind,source=$(pwd),target=/workspace \
  -w /workspace \
  compile
docker build -f $1 .
