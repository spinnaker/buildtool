# buildtool

Utilities and tools for setting up and execution Spinnaker integration tests

## Setup

```
# install python and tools
sudo apt install python3 python3-pip python3-venv

# create virtualenv to contain dependencies
python3 -m venv .venv
source .venv/bin/activate

# install dependencies
pip3 install pip --upgrade
pip3 install -r dev/requirements.txt
pip3 install -r dev/buildtool/requirements.txt

# setup PYTHONPATH for IDE's and testing
export PYTHONPATH="${PWD}/dev/"

# install debugger if required
pip3 install ipdb

# leave virtualenv
deactivate
```

## Running tests

### CI - Integration tests

See [testing/citest/README.md](testing/citest/README.md)

### Unit tests

run all unittests:

```
./unittest/run_tests.sh

```

run a specific test:

```
python ./unittest/buildtool/git_support_test.py -v
<snip>
Ran 25 tests in 2.509s

FAILED (errors=4)
```

## Release

### Create Release Branches

Create new `release-*` branches off the latest tag on `master` branch.

TODO:(kskewes-sf) - add `keel` to repository list.

```
new_branch=release-1.27.x

./dev/buildtool.sh new_release_branch \
  --git_branch master \
  --spinnaker_version "${new_branch}"
```

When troubleshooting try using your forks, targeting a single service and
disabling git push:

```
new_branch=release-0.1.x
fork_owner=<you>

./dev/buildtool.sh \
  --log_level debug \
  new_release_branch \
  --git_branch master \
  --spinnaker_version "${new_branch}" \
  --github_owner "${fork_owner}" \
  --only_repositories clouddriver \
  --git_never_push true

# check branch creation locally
cd source_code/new_release_branch/clouddriver/

git branch
  master
* release-0.2.x

# consider enabling git push and running again (remove: `--git_never_push true`)
```

### Build BOM

WARNING: If the HEAD of the new `release-*` branch is not tagged then
`buildtool` will log the following and automatically increment the latest tag's
patch version. You will need to push a new tag to HEAD of the branch and try
again.

Warning log line:

```
W 19:50:36.886 [Thread-4.552794] fiat HEAD commit of 52a8f5204dc12b693b8eac5ff6126759c119684a is newer than v1.28.4 tag at 23cf00d96d55e02ff3e4ce2d0cd42ef614532bb7
```

Build BOM, supplying a base BOM with archived project `spinnaker-monitoring`
already defined.

```
git_branch=release-1.27.x
version=1.27.0

./dev/buildtool.sh build_bom \
  --github_owner spinnaker \
  --git_branch "${git_branch}" \
  --build_number "${version}" \
  --refresh_from_bom_path dev/buildtool/bom_base.yml \
  --exclude_repositories spinnaker-monitoring
W 14:08:19.436 [MainThread.12300] Monitoring is disabled
I 14:08:19.445 [MainThread.12300] Mapping 11/['clouddriver', 'deck', 'echo', 'fiat', 'front50', 'gate', 'igor', 'kayenta', 'orca', 'rosco', 'spinnaker-monitoring']
I 14:08:19.445 [Thread-1.12300] build_bom processing clouddriver
<snip>
```

When troubleshooting try targeting a single service:

```
./dev/buildtool.sh \
  --log_level debug \
  build_bom \
  --github_owner spinnaker \
  --git_branch "${git_branch}" \
  --build_number "${version}" \
  --refresh_from_bom_path dev/buildtool/bom_base.yml \
  --exclude_repositories spinnaker-monitoring \
  --only_repositories rosco
```

Check BOM:

```
cat output/build_bom/release-1.27.x-1.27.0.yml

artifactSources:
  debianRepository: https://us-apt.pkg.dev/projects/spinnaker-community
  dockerRegistry: us-docker.pkg.dev/spinnaker-community/docker
  gitPrefix: https://github.com/spinnaker
  googleImageProject: marketplace-spinnaker-release
dependencies:
  consul:
    version: 0.7.5
  redis:
    version: 2:2.8.4-2
  vault:
    version: 0.7.0
services:
  clouddriver:
    commit: 854d708bc8e46f6c3eb5f80582ead9ed4d3f30eb
    version: 5.74.3
  deck:
    commit: f96435e0a6b0567d749f15e951ee286c6eb16ea9
    version: 3.8.1
  echo:
    commit: c73d9b8164b67c14df74bbec56504cb889587358
    version: 2.32.2
  fiat:
    commit: 9f4120cf43d4d5ebd47f48f8c845d1b97a073b40
    version: 1.28.3
  front50:
    commit: 7fbae17b319979b06221789e34f9c354ad782695
    version: 2.23.3
  gate:
    commit: b621ff317dc0d4049b1a1bc2267e61a8e0b1ce7d
    version: 6.54.1
  igor:
    commit: d17a4467233b85255db8929387f155f1615b74b7
    version: 4.6.3
  kayenta:
    commit: e946058ae6b36036e5bada984c58cd3624245071
    version: 2.31.1
  monitoring-daemon:
    commit: 91e7116c9abdf9d47acc8f25dbc349b6b9aa99f8
    version: 1.3.0
  monitoring-third-party:
    commit: 91e7116c9abdf9d47acc8f25dbc349b6b9aa99f8
    version: 1.3.0
  orca:
    commit: cbd9f141ffbd4c9cb1dc5b57c908376a7cbac8da
    version: 8.18.3
  rosco:
    commit: b539e13644b390df5b63dff30be32d2cdd1dc5f5
    version: 1.7.3
timestamp: '2022-04-19 04:18:35'
version: 1.27.0
```

### Build Changelog

Build changelog of commits in release since previous BOM versions.

A previous BOM file must be provided otherwise `buildtool` will compare the new
BOM tag to the previous tag in the same branch. Due to auto-bump PR's there may
be multiple tags on a `release-*` branch between Spinnaker Releases.

For example:

1. If releasing a new `minor` version `1.27.0` then supply previous minor
   `1.26.0`.
1. If releasing a new `patch` version `1.27.1` then supply previous patch
   `1.27.0`.

```
bom=output/build_bom/<bomfile.yml>
previous_release=<release>

# download previous BOM
mkdir -p input
wget "https://storage.googleapis.com/halconfig/bom/${previous_release}.yml" \
  "--output-document=input/${previous_release}.yml"

./dev/buildtool.sh build_changelog \
  --bom_path "${bom}" \
  --relative_to_bom_path "input/${previous_release}.yml"

```

### Push Changelog to Gist

Create a GitHub [Personal Access Token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token) which is required to push via HTTPS to a `gist`. Potentially
the code could be refactored to support `ssh` authentication.

Manually create a [gist](https://gist.github.com/spinnaker-release/about) for
raw changelog named after the release branch, eg: `release-1.27.x-raw-changelog.md`.
You can put anything in the `contents`, it will be overwritten.

Push branches raw changelog to new [spinnaker-release/about gists](https://gist.github.com/spinnaker-release/4f8cd09490870ae9ebf78be3be1763ee)

```
changelog_gist_url=<created_above>
git_branch=release-1.27.x

./dev/buildtool.sh push_changelog_to_gist \
  --build_changelog_gist_url "${changelog_gist_url}" \
  --changelog_path output/build_changelog/changelog.md \
  --git_branch "${git_branch}"
```

Create a [gist](https://gist.github.com/spinnaker-release) following the format
`M.m.p.md`, for example: `1.27.0.md`.

Follow the steps in [Release Manager Runbook](https://spinnaker.io/docs/releases/release-manager-runbook/#one-week-after-branches-are-cut-monday) to curate the changelog.

To troubleshoot, try creating your own Gist and pushing the changelog to your
fork:

```
changelog_gist_url=<created_above>
git_branch=release-1.27.x
fork_owner=<you>

./dev/buildtool.sh \
  --log_level debug \
  push_changelog_to_gist \
  --build_changelog_gist_url "${changelog_gist_url}" \
  --changelog_path output/build_changelog/changelog.md \
  --git_branch "${git_branch}" \
  --github_owner "${fork_owner}"
```

### Publish Changelog

`buildtool` will clone your fork, branch off master, commit new changelog file
and push up to GitHub. Once complete raise a PR to
[github.com/spinnaker/spinnaker.io](https://github.com/spinnaker/spinnaker/io/pulls)

```
changelog_gist_url=<created_above>
git_branch=master
spinnaker_version=1.27.0
fork_owner=<you>

./dev/buildtool.sh publish_changelog \
  --changelog_gist_url "${changelog_gist_url}" \
  --git_branch "${git_branch}" \
  --spinnaker_version "${version}" \
  --git_allow_publish_master_branch false \
  --github_owner "${fork_owner}"
```

To troubleshoot, try creating your own Gist and pushing the changelog to your
fork:

```
changelog_gist_url=<created_above>
git_branch=master
spinnaker_version=1.27.0
fork_owner=<you>

./dev/buildtool.sh \
  --log_level debug \
  publish_changelog \
  --changelog_gist_url "${changelog_gist_url}" \
  --git_branch "${git_branch}" \
  --spinnaker_version "${version}" \
  --git_allow_publish_master_branch false \
  --github_owner "${fork_owner}" \
  --git_never_push true

# check branch creation locally
cd source_code/publish_changelog/spinnaker.io

git show

# consider enabling git push and running again (remove: `--git_never_push true`)
```
