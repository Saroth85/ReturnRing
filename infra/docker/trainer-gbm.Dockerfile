# XGBoost on ROCm (AMD Finance toolkit). Base image and index URL come from the ROCm XGBoost
# install docs; both are pinned after spike SPK-G2.
ARG ROCM_BASE_IMAGE
FROM ${ROCM_BASE_IMAGE}
ARG AMD_PYPI_INDEX_URL
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
RUN test -n "$AMD_PYPI_INDEX_URL" || (echo "AMD_PYPI_INDEX_URL is required" && exit 1)
RUN pip install --no-cache-dir --extra-index-url "$AMD_PYPI_INDEX_URL" amd_xgboost pyarrow shap
WORKDIR /app
COPY policy policy
COPY contracts contracts
CMD ["python", "-c", "import xgboost; print('xgboost', xgboost.__version__)"]
