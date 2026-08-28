#!/usr/bin/env bash
set -euo pipefail

wheel_dir="${H3_BACKEND_WHEEL_DIR:-/opt/h3/wheels}"
output_dir="${H3_BACKEND_OUTPUT_DIR:-/opt/h3/backends}"
architectures="${H3_BACKEND_ARCHES:-sm86 sm89 sm90 sm120}"

for architecture in ${architectures}; do
  wheel="${wheel_dir}/sageattention-${architecture}.whl"
  target="${output_dir}/${architecture}"
  if [[ ! -f "${wheel}" ]]; then
    echo "missing backend wheel: ${wheel}" >&2
    exit 1
  fi
  rm -rf "${target}"
  mkdir -p "${target}"
  python -m pip install --no-deps --target "${target}" "${wheel}"
  printf '%s\n' "${wheel}" > "${target}/.source-wheel"
done
