# Benchmarks B1-B4 on the MI300X droplet (US-902): amd_xgboost 3.2.0 on ROCm 7.2.3 (the same
# wheel and base as trainer-gbm, so B1 times the GPU and the CPU with one xgboost build), the
# Python stack of the nightly pipeline (B4) and llm/ (B3), and amd-smi from the ROCm image.
# The code is mounted read-only at run time (infra/compose.bench.yml): a code change needs no
# rebuild. Base image and index as in trainer-gbm.Dockerfile (see .env.example).
ARG ROCM_BASE_IMAGE
FROM ${ROCM_BASE_IMAGE}
ARG AMD_PYPI_INDEX_URL
RUN apt-get update && apt-get install -y --no-install-recommends python3-venv \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv
# /opt/rocm/bin: rocminfo and amd-smi are not on the default PATH of the ROCm images.
ENV PATH=/opt/venv/bin:/opt/rocm/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN test -n "$AMD_PYPI_INDEX_URL" || (echo "AMD_PYPI_INDEX_URL is required (see .env.example)" && exit 1)
# 1. amd_xgboost alone: AMD index only, version and wheel hash pinned, no dependencies.
COPY infra/docker/requirements-gbm-amd.txt /tmp/requirements-gbm-amd.txt
RUN pip install --no-cache-dir --no-deps --require-hashes --index-url "$AMD_PYPI_INDEX_URL" \
    -r /tmp/requirements-gbm-amd.txt
# 2. Everything else: PyPI only, every package pinned, then a consistency check.
COPY infra/docker/requirements-bench.txt /tmp/requirements-bench.txt
RUN pip install --no-cache-dir --no-deps --index-url https://pypi.org/simple \
    -r /tmp/requirements-bench.txt \
    && pip check
# 3. Fail the build, not the benchmark, if the wheel is not the HIP build or a ROCm library of
#    libxgboost.so is missing.
RUN lib="$(python -c "import importlib.util as u, pathlib; print(pathlib.Path(u.find_spec('xgboost').origin).parent / 'lib' / 'libxgboost.so')")" \
    && test -f "$lib" \
    && ! ldd "$lib" | grep 'not found' \
    && python -c "import xgboost as x; assert x.build_info().get('USE_HIP'), x.build_info(); print('xgboost', x.__version__, 'USE_HIP')" \
    && python -c "import polars, pyarrow, sklearn, networkx, pydantic; print('bench stack ok')"
WORKDIR /app
CMD ["python", "-m", "bench", "--help"]
