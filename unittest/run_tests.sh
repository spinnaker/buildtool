#!/usr/bin/env bash

set -o nounset -o pipefail

cd "$(dirname "$0")" || exit 1
TEST_DIR=$(pwd)

# Some tests need to be in the build directory
# so run them all from there.
cd "$TEST_DIR"/../.. || exit 1

passed_tests=()
failed_tests=()

for test in $(
	cd "$TEST_DIR" || exit 1
	find . -name \*_test.py -print
); do
	echo "Running $test"
	PYTHONPATH="$TEST_DIR"/../dev python "$TEST_DIR/$test"

	if [[ $? -eq 0 ]]; then
		passed_tests+=("$test")
	else
		failed_tests+=("$test")
	fi
done

if [[ ${#failed_tests[@]} -eq 0 ]]; then
	echo "PASSED ALL ${#passed_tests[@]} TESTS"
	exit 0
fi

echo >&2 "FAILED ${#failed_tests[@]} TESTS from $TEST_DIR"
for test in "${failed_tests[@]}"; do
	echo >&2 "FAILED: $test"
done
exit 1
