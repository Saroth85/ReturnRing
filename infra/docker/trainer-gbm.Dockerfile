# XGBoost on ROCm (AMD Finance toolkit 26.05): amd_xgboost 3.2.0 on ROCm 7.2.3, Ubuntu 24.04.
# Base image and index URL as in https://rocm.docs.amd.com/projects/xgboost/en/docs-26.05/install/install.html
# (ROCM_BASE_IMAGE=rocm/dev-ubuntu-24.04:7.2.3-complete@sha256:..., see .env.example).
ARG ROCM_BASE_IMAGE
FROM ${ROCM_BASE_IMAGE}
ARG AMD_PYPI_INDEX_URL
RUN apt-get update && apt-get install -y --no-install-recommends python3-venv \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv
# /opt/rocm/bin: rocminfo and amd-smi are not on the default PATH of the ROCm images.
ENV PATH=/opt/venv/bin:/opt/rocm/bin:$PATH
RUN test -n "$AMD_PYPI_INDEX_URL" || (echo "AMD_PYPI_INDEX_URL is required (see .env.example)" && exit 1)
# 1. amd_xgboost alone: AMD index only, version and wheel hash pinned, no dependencies.
COPY infra/docker/requirements-gbm-amd.txt /tmp/requirements-gbm-amd.txt
RUN pip install --no-cache-dir --no-deps --require-hashes --index-url "$AMD_PYPI_INDEX_URL" \
    -r /tmp/requirements-gbm-amd.txt
# 2. Everything else: PyPI only, every package pinned, then a consistency check.
COPY infra/docker/requirements-gbm.txt /tmp/requirements-gbm.txt
RUN pip install --no-cache-dir --no-deps --index-url https://pypi.org/simple \
    -r /tmp/requirements-gbm.txt \
    && pip check
# 3. Fail the build, not the spike, if a ROCm library of libxgboost.so is missing (librccl.so.1
#    comes with rocm-libs, which the -complete base has) or the wheel is not the HIP build.
RUN lib="$(python -c "import importlib.util as u, pathlib; print(pathlib.Path(u.find_spec('xgboost').origin).parent / 'lib' / 'libxgboost.so')")" \
    && test -f "$lib" \
    && ! ldd "$lib" | grep 'not found' \
    && python -c "import xgboost as x; assert x.build_info().get('USE_HIP'), x.build_info(); print('xgboost', x.__version__, 'USE_HIP')"
WORKDIR /app
COPY policy policy
COPY contracts contracts
CMD ["python", "-c", "import xgboost; print('xgboost', xgboost.__version__)"]
