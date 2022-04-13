# buildtool

Utilities and tools for setting up and execution Spinnaker integration tests

## Setup

Whilst there have been some commits adding python3 support it looks like
python2 may still be required for some tests. TBC.

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
cd unittest/buildtool

PYTHONPATH=$(pwd)/../../dev/ python $(pwd)/git_support_test.py -v
<snip>
Ran 25 tests in 2.509s

FAILED (errors=4)
```

## Release

### BOM Generation

Build BOM:

```
git_branch=release-1.27.x

./dev/buildtool.sh build_bom \
  --github_owner spinnaker \
  --git_branch "${git_branch}"
W 14:08:19.436 [MainThread.12300] Monitoring is disabled
I 14:08:19.445 [MainThread.12300] Mapping 11/['clouddriver', 'deck', 'echo', 'fiat', 'front50', 'gate', 'igor', 'kayenta', 'orca', 'rosco', 'spinnaker-monitoring']
I 14:08:19.445 [Thread-1.12300] build_bom processing clouddriver
<snip>
```

When troubleshooting try targeting a single service:

```
./dev/buildtool.sh build_bom \
  --log_level debug \
  --github_owner spinnaker \
  --git_branch "${git_branch}" \
  --only_repositories rosco
```

Check BOM:

```
cat output/build_bom/release-1.27.x-20220404020819.yml

artifactSources:
  gitPrefix: https://github.com/spinnaker
dependencies:
  consul:
    version: 0.7.5
  redis:
    version: 2:2.8.4-2
  vault:
    version: 0.7.0
services:
  clouddriver:
    commit: b4e1db9641b68dad506e9b9a10a49d7c17c58b51
    version: 8.1.0-20220404020819
  deck:
    commit: f96435e0a6b0567d749f15e951ee286c6eb16ea9
    version: 3.8.0-20220404020819
  echo:
    commit: c73d9b8164b67c14df74bbec56504cb889587358
    version: 2.18.0-20220404020819
  fiat:
    commit: 9f4120cf43d4d5ebd47f48f8c845d1b97a073b40
    version: 1.17.0-20220404020819
  front50:
    commit: 7fbae17b319979b06221789e34f9c354ad782695
    version: 0.28.0-20220404020819
  gate:
    commit: b621ff317dc0d4049b1a1bc2267e61a8e0b1ce7d
    version: 1.23.0-20220404020819
  igor:
    commit: d17a4467233b85255db8929387f155f1615b74b7
    version: 1.18.1-20220404020819
  kayenta:
    commit: e946058ae6b36036e5bada984c58cd3624245071
    version: 0.22.0-20220404020819
  monitoring-daemon:
    commit: ede1d75c0595e172924e7b985b189e48598aa581
    version: 0.19.4-20220404020819
  monitoring-third-party:
    commit: ede1d75c0595e172924e7b985b189e48598aa581
    version: 0.19.4-20220404020819
  orca:
    commit: cbd9f141ffbd4c9cb1dc5b57c908376a7cbac8da
    version: 2.21.0-20220404020819
  rosco:
    commit: b539e13644b390df5b63dff30be32d2cdd1dc5f5
    version: 0.26.0-20220404020819
timestamp: '2022-04-04 02:09:08'
version: release-1.27.x-20220404020819
```

### Changelog Generation

Generate changelog of release branch commits included in BOM:

```
bom=output/build_bom/<bomfile.yml>

./dev/buildtool.sh build_changelog --bom_path "${bom}"
```

Manually create a [gist](https://gist.github.com/spinnaker-release/about) for
raw changelog named after the release branch, eg: `release-1.27.x-raw-changelog.md`.

Push branches raw changelog to new [spinnaker-release/about gists](https://gist.github.com/spinnaker-release/4f8cd09490870ae9ebf78be3be1763ee)

```
changelog_gist_url=<created_above>
git_branch=release-1.27.x

./dev/buildtool.sh push_changelog_to_gist \
  --build_changelog_gist_url "${changelog_gist_url}"
  --changelog_path output/build_changelog/changelog.md \
  --git_branch "${git_branch}" \
```

Create a [gist](https://gist.github.com/spinnaker-release) following the format
`M.m.p.md`, for example: `1.27.0.md`.

Follow the steps in [Release Manager Runbook](https://spinnaker.io/docs/releases/release-manager-runbook/#one-week-after-branches-are-cut-monday) to curate the changelog.
