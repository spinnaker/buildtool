#!/usr/bin/env bash

set -o errexit -o nounset -o pipefail

# Set and uncomment these lines to run script
# git_branch=release-1.29.x
# version=1.29.0

# Disable any cloud resource upserts - docker push, gcs upload, etc
dryrun="true"

# Below are unlikely to require changing
bom="output/build_bom/${git_branch}-${version}.yml"
versions_file=versions.yml
bucket=halconfig
registry=us-docker.pkg.dev/spinnaker-community/docker
services=(
	"clouddriver"
	"deck"
	"echo"
	"fiat"
	"front50"
	"gate"
	"igor"
	"kayenta"
	"monitoring_daemon"
	"orca"
	"rosco"
)

build_bom() {
	echo "NOT IMPLEMENTED"
}

build_changelog() {
	echo "NOT IMPLEMENTED"
}

upload_bom() {
	if [ "${dryrun}" == "true" ]; then
		echo "WARNING: not uploading BOM ${version}.yml"
		echo gsutil cp "${bom}" "gs://${bucket}/bom/${version}.yml"
		return
	fi

	gsutil cp "${bom}" "gs://${bucket}/bom/${version}.yml"
}

update_versions() {
	# add 000 to date to match spinrel code: `lastUpdate = Instant.now().truncatedTo(ChronoUnit.MILLIS)`
	echo "lastUpdate: $(date +%s)000"

	echo "INCOMPLETE: not updating versions.yml"
}

upload_versions() {
	if [ "${dryrun}" == "true" ]; then
		echo "WARNING: not uploading versions.yml"
		echo gsutil cp "${versions_file}" "gs://${bucket}/versions.yml"
		return
	fi

	gsutil cp "${versions_file}" "gs://${bucket}/versions.yml"
}

generate_version_mappings() {
	echo "generating version mapping"
	mkdir -p ./input

	# convert to json first as can't get yq expression to work
	yq -o json <"${bom}" |
		# build mapping of service=version to source for tagging
		jq -r '.services | keys[] as $key | "\($key)=\(.[$key].version)"' |
		# ignore monitoring-third-party
		grep -v 'monitoring-third-party' |
		sed 's/monitoring-daemon/monitoring_daemon/' \
			>./input/mappings.sh
}

tag_containers() {
	# shellcheck disable=SC1091
	source ./input/mappings.sh

	for service in "${services[@]}"; do
		tag="${!service}"

		if [ "${service}" == "monitoring_daemon" ]; then
			# in order to source mappings.sh we had renamed monitoring-daemon to monitoring_daemon
			# here we switch it back as the container name is monitoring-daemon
			service="monitoring-daemon"
		fi

		echo -e "\nTagging ${service} containers at '${tag}' and 'spinnaker-${version}'"

		echo regctl image copy "${registry}/${service}:${tag}-unvalidated" "${registry}/${service}:${tag}"
		echo regctl image copy "${registry}/${service}:${tag}-unvalidated" "${registry}/${service}:spinnaker-${version}"

		echo regctl image copy "${registry}/${service}:${tag}-unvalidated-ubuntu" "${registry}/${service}:${tag}-ubuntu"
		echo regctl image copy "${registry}/${service}:${tag}-unvalidated-ubuntu" "${registry}/${service}:spinnaker-${version}-ubuntu"

		if [ "${dryrun}" == "true" ]; then
			echo "WARNING: not pushing containers for ${service}"
			continue
		fi

		regctl image copy "${registry}/${service}:${tag}-unvalidated" "${registry}/${service}:${tag}"
		regctl image copy "${registry}/${service}:${tag}-unvalidated" "${registry}/${service}:spinnaker-${version}"

		regctl image copy "${registry}/${service}:${tag}-unvalidated-ubuntu" "${registry}/${service}:${tag}-ubuntu"
		regctl image copy "${registry}/${service}:${tag}-unvalidated-ubuntu" "${registry}/${service}:spinnaker-${version}-ubuntu"

		echo -e "\n"
	done
}

main() {
	build_bom
	build_changelog
	generate_version_mappings
	tag_containers
	upload_bom
	update_versions
	upload_versions
}

main
