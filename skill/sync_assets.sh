#!/usr/bin/env bash
# Regenerates the skill's self-contained assets/tests from the canonical
# source in ../adapter/. Run this after any change to adapter/sgmcp_adapter/
# or adapter/pyproject.toml, BEFORE re-publishing the skill version - the
# published artifact must not depend on this repo existing.
set -euo pipefail
cd "$(dirname "$0")"

rm -rf supabase-employee-integration/assets/sgmcp_adapter
cp -R ../adapter/sgmcp_adapter supabase-employee-integration/assets/sgmcp_adapter
cp ../adapter/pyproject.toml supabase-employee-integration/assets/pyproject.toml

rm -rf supabase-employee-integration/tests
cp -R ../adapter/tests supabase-employee-integration/tests

rm -rf supabase-employee-integration/assets/frontend
cp -R ../adapter/frontend supabase-employee-integration/assets/frontend

cp ../adapter/integration_tests/test_adapter_integration.py supabase-employee-integration/checks/test_adapter_integration.py

echo "Synced adapter/ -> skill/supabase-employee-integration/{assets,tests,checks}"
