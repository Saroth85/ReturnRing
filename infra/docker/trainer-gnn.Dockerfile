# trainer-graphsage: the GraphSAGE jobs of US-202 and A4 (python -m ml.gnn) on the DGL ROCm stack.
# Same base as trainer-gnn (ADR-01: one ROCm stack, rocm/dgl: DGL 2.4, PyTorch 2.8, ROCm 7.0,
# Python 3.12 in /opt/venv), plus this repository's code and the few packages it needs on top.
# DGL_IMAGE is pinned by tag and digest in .env.example; the image is never pushed (ADR-12).
ARG DGL_IMAGE
FROM ${DGL_IMAGE}
# torch, DGL and scipy stay as the image ships them (built together for ROCm). The image's numpy is
# 1.26.4 (amd-dgl pins it) and the jobs need numpy 2, so numpy, pandas and the other packages the
# jobs, B2 and the GPU tests import are installed, pinned, from PyPI, without dependencies
# (infra/docker/requirements-gnn.txt says why each one is there).
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
COPY bench bench
COPY tests tests
# Fail the build, not the GPU session: numpy is the pinned 2.5.4, DGL still works on it (a graph
# and a SAGEConv forward pass on the CPU), and every module of the jobs, B2 and the GPU tests
# imports.
RUN python -c "import numpy, sys; print('numpy', numpy.__version__); sys.exit(0 if numpy.__version__ == '2.5.4' else 'numpy 2.5.4 is needed (requirements-gnn.txt)')" \
    && python -c "import dgl, numpy as np, torch; from dgl.nn import SAGEConv; g = dgl.graph((np.array([0, 1, 2]), np.array([1, 2, 0]))); h = SAGEConv(4, 2, 'mean')(g, torch.ones(3, 4)); assert tuple(h.shape) == (3, 2); print('dgl', dgl.__version__, 'on numpy', np.__version__)" \
    && python -c "import dgl, torch, scipy, sklearn, xgboost, polars, pyarrow, pandas, hypothesis; import ml.gnn.asos, ml.gnn.embeddings, ml.gnn.report, bench.b2_gnn, tests.conftest; print('torch', torch.__version__, 'hip', torch.version.hip, 'dgl', dgl.__version__, 'xgboost', xgboost.__version__, 'polars', polars.__version__, 'pandas', pandas.__version__)"
CMD ["python", "-m", "ml.gnn", "--help"]
