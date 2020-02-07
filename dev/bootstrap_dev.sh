#!/bin/bash
#
# Copyright 2015 Google Inc. All Rights Reserved.
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


# About this script:
# -----------------
# This script prepares a user environment to build spinnaker from source.
# It is intended to only be run one time. Typically on a newly provisioned
# GCE instance, but could be run on any linux machine.
#
# What it does:
# ------------
# This script will do the following:
#
#   * Create a $HOME/.git-credentials file if one does not already exist
#       You will be prompted for your github username and two-factor access
#       token.
#
#   * Creates a build/ directory as a subdirectory of $PWD.
#
#   * Clone each of the spinnaker subsystem github repositories into build/
#
#       When a repository is cloned, an upstream remote will be added to
#       reference the authoritative repository for the given repository.
#       (e.g. the spinnaker github repository corresponding to your origin).
#
#       If the environment variable GITHUB_REPOSITORY_OWNER is set then
#       the repositories will be cloned from that github user. Otherwise it
#       will be cloned from the user in your .git-credentials. If the owner
#       is "upstream" then it will clone the authoritative repository for each
#       repository cloned (e.g. all the 'spinnaker' repositories).
#
#    * Print out next step instructions.
#
#
# Running the script:
# -------------------
# Rather than executing the script, you can source it to leave some side
# effects in the current shell, such as path changes for new installed
# components.


function prompt_YN() {
  def=$1
  msg=$2
  if [[ "$def" == "Y" ]]; then
      local choice="Y/n"
  else
      local choice="y/N"
  fi

  while true; do
      got=""
      read -p "$msg [$choice]: " got
      if [[ "$got" == "" ]]; then
          got=$def
      fi
      if [[ "$got" == "y" || "$got" == "Y" ]]; then
        return 0
      fi
      if [[ "$got" == "n" || "$got" == "N" ]]; then
        return 1
      fi
  done
}


function git_clone() {
  local git_user="$1"
  local git_project="$2"
  local upstream_user="$3"

  if [[ "$git_user" == "default" || "$git_user" == "upstream" ]]; then
      git_user="$upstream_user"
  fi
  git clone https://github.com/$git_user/$git_project.git

  if [[ "$github_user" == "$upstream_user" ]]; then
    git -C $git_project remote set-url --push origin disabled
  else
    git -C $git_project remote add upstream \
        https://github.com/$upstream_user/${git_project}.git
    git -C $git_project remote set-url --push upstream disabled
  fi
}

function prepare_git() {
  # If you do not have a .git-credentials file, you might want to create one.
  # You were better off doing this on your original machine because then
  # it would have been copied here (and to future VMs created by this script).
  if [[ -f ~/.git-credentials ]]; then
    GITHUB_USER=$(sed 's/https:\/\/\([^:]\+\):.*@github.com/\1/' ~/.git-credentials)
  else
    GITHUB_USER=""
    while [[ "$GITHUB_USER" == "" ]]; do
      read -p 'Please enter your GitHub User ID (or 'none'): ' GITHUB_USER
    done
    if [[ "$GITHUB_USER" != "none" ]]; then
      read -p 'Please enter your GitHub Access Token: ' ACCESS_TOKEN
      cat <<EOF > ~/.git-credentials
https://$GITHUB_USER:$ACCESS_TOKEN@github.com
EOF
      chmod 600 ~/.git-credentials

      if prompt_YN "Y" "Cache git credentials?"; then
        git config --global credential.helper store
      fi
    fi
  fi

  # If specified then use this as the user owning github repositories when
  # cloning them. If the owner is "upstream" then use the default owner for the
  # given repository. If this is not defined, then use GITHUB_USER which is
  # intended to be the github user account for the user running this script.
  GITHUB_REPOSITORY_OWNER=${GITHUB_REPOSITORY_OWNER:-"$GITHUB_USER"}

  # Select repository
  # Inform that "upstream" is a choice
  cat <<EOF

When selecting a repository owner, you can use "upstream" to use
each of the authoritative repositories rather than your own forks.
However, you will not be able to push any changes "upstream".
This selection is only used if this script will be cloning repositories.
If you want to use your own pre-existing forks, enter "$GITHUB_REPOSITORY_OWNER".
To fork a repository, goto the repository page on http://github.com.

EOF
  read -p "Github repository owner [upstream] " \
    CONFIRMED_GITHUB_REPOSITORY_OWNER
  if [[ "$CONFIRMED_GITHUB_REPOSITORY_OWNER" == "" ]]; then
    CONFIRMED_GITHUB_REPOSITORY_OWNER=upstream
  fi
}


#####################
# Start script

# Install node
NODE_VERSION=8.9.0
. /etc/profile.d/nvm.sh
nvm install $NODE_VERSION
nvm alias default $NODE_VERSION

# Install packer
have_packer=$(which packer)
if [[ ! $have_packer ]]; then
  echo "Installing packer"
  url=https://releases.hashicorp.com/packer/0.12.1/packer_0.12.1_linux_amd64.zip
  pushd $HOME
  if ! curl -s --location -O "$url"; then
     popd
     echo "Failed downloading $url"
     exit -1
  fi
  unzip $(basename $url) -d packer > /dev/null
  rm -f $(basename $url)
  popd

  export PATH=$PATH:$HOME/packer
  if prompt_YN "Y" "Update .bash_profile to add $HOME/packer to your PATH?"; then
     echo "PATH=\$PATH:\$HOME/packer" >> $HOME/.bash_profile
  fi
fi


# setup Halyard
curl -q -O \
    https://raw.githubusercontent.com/spinnaker/halyard/master/install/nightly/InstallHalyard.sh
chmod +x InstallHalyard.sh
if prompt_YN "Y" "Install Halyard (as user $LOGNAME)?"; then
    sudo ./InstallHalyard.sh --user $LOGNAME
fi

# Setup git
prepare_git
prepare_python


# setup Google SDK
if prompt_YN "Y" "Install (or update) Google Cloud Platform SDK?"; then
   # Download gcloud to ensure it is a recent version.
   # Note that this is in this script because the gcloud install method isn't
   # system-wide. The awscli is installed in the install_development.sh script.
   pushd $HOME
   echo "*** REMOVING pre-installed gcloud..."
   sudo apt-get remove google-cloud-sdk -y
   echo "*** BEGIN installing gcloud..."
   curl https://sdk.cloud.google.com | bash

   if [[ -f $HOME/.bashrc ]]; then
     echo "Re-sourcing .bashrc to pick up path changes"
     source $HOME/.bashrc
   fi

   echo "Adding kubectl..."
   gcloud components install kubectl -q || true

   if [[ ! -f $HOME/.config/gcloud/credentials ]]; then
      echo "Running gcloud authentication..."
      gcloud auth login
   else
      echo "*** Using existing gcloud authentication:"
      gcloud auth list
      echo "To add another account, run gcloud auth login"
   fi
   echo "*** FINISHED installing gcloud..."
   popd
fi

# Setup AWS SDK
# If awscli isn't installed, give a second chance here for consistency
if ! aws --version >& /dev/null && prompt_YN "Y" "Install AWS Platform SDK?"; then
    sudo apt-get install -y awscli
fi

# Setup Azure SDK
# If azure-cli isn't installed, give a second chance here for consistency
if ! azure-cli --version >& /dev/null && prompt_YN "N" "Install Azure Platform SDK?"; then
    # https://docs.microsoft.com/en-us/cli/azure/install-azure-cli#apt-get
    echo "deb [arch=amd64] https://packages.microsoft.com/repos/azure-cli/ wheezy main" | \
         sudo tee /etc/apt/sources.list.d/azure-cli.list
    sudo apt-key adv --keyserver packages.microsoft.com --recv-keys 417A0893
    sudo apt-get install apt-transport-https
    sudo apt-get update
    sudo apt-get install -y azure-cli
    az login
fi

# Setup source code
if [[ "$CONFIRMED_GITHUB_REPOSITORY_OWNER" != "none" ]]; then
  mkdir -p build
  cd build
  echo "Setting up Spinnaker source code in $PWD"

  # This is a bootstrap pull of the development scripts.
  if [[ ! -e "spinnaker" ]]; then
    existing_spinnaker=false
    git_clone $CONFIRMED_GITHUB_REPOSITORY_OWNER "spinnaker" "spinnaker"
  else
    existing_spinnaker=true
    echo "spinnaker/ already exists. Don't clone it."
  fi

  echo "Setting up buildtool virtual environment in $PWD/venv"
  virtualenv venv
  source venv/bin/activate
  pip install --upgrade pip

  # Pull the spinnaker source into a fresh build directory.
  pip install -r ./buildtool/dev/requirements.txt
  pip install -r ./buildtool/dev/buildtool/requirements.txt
  ./buildtool/dev/buildtool.sh --input_dir=tmp.in --output_dir=tmp.out \
      fetch_source \
      --github_disable_upstream_push true \
      --github_owner $CONFIRMED_GITHUB_REPOSITORY_OWNER

  if [[ "$existing_spinnaker" == "true" ]]; then
     rm -rf tmp.in/fetch_source/spinnaker
  else
     # Remove the bootstrap repo
     rm -rf spinnaker
  fi

  # These are all the fetched git repos for spinnaker
  mv tmp.in/fetch_source/* .

  # Cleanup from our build script
  rm -rf tmp.in tmp.out
fi

# If this script was run in a different shell then we
# don't have the environment variables we set, and aren't in the build directory.
function print_invoke_instructions() {
cat <<EOF


EOF
if [[ $(basename "/bogus/$0") == "bootstrap_dev.sh" ]]; then
cat <<EOF
Restart your shell to pick up path changes:
  exec $SHELL

EOF
fi

cat <<EOF
For more information,
see https://www.spinnaker.io/guides/developer/getting-set-up/
EOF
}


# If we sourced this script, we already have a bunch of stuff setup.
function print_source_instructions() {
cat <<EOF

For more information,
see https://www.spinnaker.io/guides/developer/getting-set-up/
EOF
}


function print_spinnaker_reference() {
cat <<EOF

For more information about Spinnaker, see http://spinnaker.io

EOF
}


if [[ $(basename "/bogus/$0") == "bootstrap_dev.sh" ]]; then
  print_invoke_instructions
else
  print_source_instructions
fi

print_spinnaker_reference

if [[ $(basename "/bogus/$0") != "bootstrap_dev.sh" ]]; then
  echo "Restarting shell to pick up path changes."
  exec $SHELL -l
fi
