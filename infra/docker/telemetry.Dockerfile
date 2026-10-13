# Samples amd-smi every 5 s for the admin panel (US-905). amd-smi ships with ROCm images.
ARG ROCM_BASE_IMAGE
FROM ${ROCM_BASE_IMAGE}
WORKDIR /app
ENV PATH=/opt/rocm/bin:$PATH
