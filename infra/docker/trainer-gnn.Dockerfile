# trainer-graphsage: the GraphSAGE jobs of US-202 and A4 (python -m ml.gnn) on the DGL ROCm stack.
# Same base as trainer-gnn (ADR-01: one ROCm stack, rocm/dgl: DGL 2.4, PyTorch 2.8, ROCm 7.0,
# Python 3.12 in /opt/venv), plus this repository's code and the few packages it needs on top.
# DGL_IMAGE is pinned by tag and digest in .env.example; the image is never pushed (ADR-12).
ARG DGL_IMAGE
FROM ${DGL_IMAGE}
# torch, DGL, numpy, scipy, pandas and networkx stay as the image ships them (DGL was built and
# tested against them); only our extra packages are installed, pinned, from PyPI, without
# dependencies (infra/docker/requirements-gnn.txt).
COPY infra/docker/requirements-gnn.txt /tmp/requirements-gnn.txt
RUN python -m pip install --no-cache-dir --no-deps --index-url https://pypi.org/simple \
    -r /tmp/requirements-gnn.txt
WORKDIR /app
ENV DGLBACKEND=pytorch \
    PYTHONDONTWRITEBYTECODE=1
COPY pyproject.toml pyproject.toml
COPY contracts contracts
COPY policy policy
COPY pipeline pipeline
COPY ml ml
COPY tests tests
# Fail the build, not the GPU session: every module of the jobs must import, with numpy 2 (the
# ingest and the propensity code need it). If this fails on numpy, add numpy==2.5.4 and
# pandas==3.0.6 to requirements-gnn.txt and rebuild.
RUN python -c "import numpy, sys; v = numpy.__version__; print('numpy', v); sys.exit(0 if int(v.split('.')[0]) >= 2 else 'numpy >= 2 is needed')" \
    && python -c "import dgl, torch, scipy, sklearn, xgboost, polars, pyarrow; import ml.gnn.asos, ml.gnn.embeddings, ml.gnn.report; print('torch', torch.__version__, 'hip', torch.version.hip, 'dgl', dgl.__version__, 'xgboost', xgboost.__version__, 'polars', polars.__version__)"
CMD ["python", "-m", "ml.gnn", "--help"]
