"""GraphSAGE layers of ml/gnn/model.py (US-202): the pure-PyTorch layers are dgl.nn.SAGEConv,
on tiny graphs, on the CPU. Needs PyTorch (marker torch, skipped without it); the comparison with
DGL runs where DGL is importable (the trainer-graphsage image), on the CPU and, with the gpu
marker, on the MI300X. Every tensor here is random test data.
"""

from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from ml.gnn import runtime  # noqa: E402
from ml.gnn.graph import bipartite  # noqa: E402
from ml.gnn.model import (  # noqa: E402
    EdgeClassifier,
    Encoder,
    EncoderConfig,
    TorchSAGEConv,
    to_device_graph,
    torch_relation,
)
from ml.gnn.train import TrainConfig, edge_set, fit  # noqa: E402

pytestmark = pytest.mark.torch
RELATIONS = (("customer", "buys", "variant"), ("variant", "bought_by", "customer"))


def random_graph(seed: int = 0, n_c: int = 30, n_v: int = 12, e: int = 80) -> tuple:
    rng = np.random.default_rng(seed)
    c = rng.integers(0, n_c - 3, e)  # the last 3 customers have no edge
    v = rng.integers(0, n_v, e)
    graph = bipartite("customer", "variant", "buys", "bought_by", c, v, n_c, n_v)
    gen = torch.Generator().manual_seed(seed)
    x = {
        "customer": torch.randn(n_c, 5, generator=gen),
        "variant": torch.randn(n_v, 20, generator=gen),
    }
    return graph, x, c, v


def test_mean_is_the_average_of_the_in_neighbours_with_its_gradient() -> None:
    gen = torch.Generator().manual_seed(1)
    src = torch.randint(0, 40, (150,), generator=gen)
    dst = torch.randint(0, 25, (150,), generator=gen)
    rel = torch_relation("a", "r", "b", src, dst, 40, 30)  # destinations 25..29: no in-edge
    h = torch.randn(40, 4, generator=gen, requires_grad=True)
    degree = torch.bincount(dst, minlength=30).clamp(min=1).unsqueeze(1)
    reference = torch.zeros(30, 4).index_add(0, dst, h[src]) / degree
    out = rel.mean(h)
    assert torch.allclose(out, reference, atol=1e-6)
    assert torch.all(out[25:] == 0)
    w = torch.randn(30, 4, generator=gen)
    (g1,) = torch.autograd.grad((out * w).sum(), h)
    (g2,) = torch.autograd.grad((reference * w).sum(), h)
    assert torch.allclose(g1, g2, atol=1e-6)


def test_max_is_the_elementwise_maximum_of_the_in_neighbours() -> None:
    src = torch.tensor([0, 1, 2, 2])
    dst = torch.tensor([0, 0, 1, 0])
    rel = torch_relation("a", "r", "b", src, dst, 3, 3)
    h = torch.tensor([[1.0, -5.0], [3.0, -1.0], [2.0, -2.0]])
    out = rel.max(h)
    assert out.tolist() == [[3.0, -1.0], [2.0, -2.0], [0.0, 0.0]]


@pytest.mark.parametrize("in_src", [3, 24])  # below and above the output size: both orders
def test_sage_conv_mean_is_dgl_formula(in_src: int) -> None:
    torch.manual_seed(0)
    conv = TorchSAGEConv((in_src, 7), 16, "mean").eval()
    src, dst = torch.tensor([0, 1, 1, 2]), torch.tensor([0, 0, 1, 1])
    rel = torch_relation("a", "r", "b", src, dst, 3, 3)
    hs, hd = torch.randn(3, in_src), torch.randn(3, 7)
    neigh = torch.stack([hs[[0, 1]].mean(0), hs[[1, 2]].mean(0), torch.zeros(in_src)])
    expected = conv.fc_self(hd) + neigh @ conv.fc_neigh.weight.T
    assert torch.allclose(conv(rel, (hs, hd)), expected, atol=1e-5)
    assert conv.fc_neigh.bias is None and conv.fc_self.bias is not None


def test_parameter_names_are_those_of_dgl_heterographconv() -> None:
    config = EncoderConfig((("customer", 5), ("variant", 20)), RELATIONS, aggregator="pool")
    names = sorted(Encoder(config, "torch").state_dict())
    assert names == sorted(
        f"layers.0.mods.{rel}.{p}"
        for rel in ("buys", "bought_by")
        for p in (
            "fc_pool.weight",
            "fc_pool.bias",
            "fc_neigh.weight",
            "fc_self.weight",
            "fc_self.bias",
        )
    )


@pytest.mark.parametrize("aggregator", ["mean", "pool"])
def test_the_dgl_and_torch_backends_are_the_same_model(aggregator: str) -> None:
    pytest.importorskip("dgl")
    _same_model(aggregator, torch.device("cpu"))


@pytest.mark.gpu
@pytest.mark.parametrize("aggregator", ["mean", "pool"])
def test_the_dgl_and_torch_backends_are_the_same_model_on_the_gpu(aggregator: str) -> None:
    pytest.importorskip("dgl")
    if not torch.cuda.is_available():
        pytest.skip("no GPU visible")
    _same_model(aggregator, torch.device("cuda", 0))


def _same_model(aggregator: str, device: torch.device) -> None:
    graph, x, _, _ = random_graph()
    x = {k: v.to(device) for k, v in x.items()}
    config = EncoderConfig(
        (("customer", 5), ("variant", 20)), RELATIONS, layers=2, aggregator=aggregator
    )
    torch.manual_seed(3)
    with_dgl = Encoder(config, "dgl").to(device).eval()
    torch.manual_seed(3)
    with_torch = Encoder(config, "torch").to(device).eval()
    a, b = with_dgl.state_dict(), with_torch.state_dict()
    assert a.keys() == b.keys()
    for key in a:  # same seed, same initial weights
        assert torch.equal(a[key], b[key]), key
    out_dgl = with_dgl(to_device_graph(graph, "dgl", device), x)
    out_torch = with_torch(to_device_graph(graph, "torch", device), x)
    for node_type in ("customer", "variant"):
        assert torch.allclose(out_dgl[node_type], out_torch[node_type], atol=1e-5), node_type
    with_torch.load_state_dict(with_dgl.state_dict())  # weights move between backends


def test_auto_backend_falls_back_to_pytorch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "dgl_available", lambda: False)
    assert runtime.resolve_backend("auto") == "torch"
    with pytest.raises(RuntimeError):
        runtime.resolve_backend("dgl")
    with pytest.raises(ValueError):
        runtime.resolve_backend("tensorflow")


def test_a_gpu_run_without_a_gpu_is_refused() -> None:
    if torch.cuda.is_available():
        pytest.skip("a GPU is visible here")
    with pytest.raises(runtime.GpuUnavailable):
        runtime.resolve_device("gpu")
    assert runtime.resolve_device("cpu").type == "cpu"


def test_device_follows_the_device_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVICE", "gpu")
    assert runtime.default_device() == "gpu"
    monkeypatch.setenv("DEVICE", "cpu")
    assert runtime.default_device() == "cpu"


def _tiny_fit(seed: int) -> tuple[list[float], dict]:
    graph, x, c, v = random_graph(seed=4)
    rng = np.random.default_rng(4)
    y = rng.integers(0, 2, c.size).astype(np.int8)
    ex = rng.normal(size=(c.size, 3)).astype(np.float32)
    cpu = torch.device("cpu")
    train = edge_set(c[:60], v[:60], ex[:60], y[:60], cpu)
    valid = edge_set(c[60:], v[60:], ex[60:], y[60:], cpu)
    runtime.seed_everything(seed)
    config = EncoderConfig((("customer", 5), ("variant", 20)), RELATIONS)
    model = EdgeClassifier(Encoder(config, "torch"), (16, 16), 3)
    g = to_device_graph(graph, "torch", cpu)
    result = fit(
        model, g, x, train, valid, TrainConfig(max_epochs=4, batch_size=16, seed=seed), cpu
    )
    return result.valid_loss, model.state_dict()


def test_training_on_the_cpu_is_repeatable() -> None:
    loss_a, state_a = _tiny_fit(42)
    loss_b, state_b = _tiny_fit(42)
    assert loss_a == loss_b
    assert all(torch.equal(state_a[k], state_b[k]) for k in state_a)
    loss_c, _ = _tiny_fit(7)
    assert loss_c != loss_a


def test_the_mlp_control_reads_the_input_features() -> None:
    graph, x, c, v = random_graph()
    model = EdgeClassifier(None, (5, 20), 2).eval()
    h = model.nodes(None, x)
    assert h["customer"] is x["customer"]
    out = model.logits(h, torch.from_numpy(c[:4]), torch.from_numpy(v[:4]), torch.zeros(4, 2))
    assert out.shape == (4,)


def test_stopwatch_and_environment() -> None:
    cpu = torch.device("cpu")
    watch = runtime.Stopwatch(cpu)
    assert watch.time("phase", lambda: 3) == 3
    assert "phase" in watch.rounded()
    assert runtime.warm_up(cpu) >= 0
    env = runtime.environment(cpu, "torch")
    assert env["device"] == "cpu" and env["backend"] == "torch" and env["torch"]
    assert runtime.gpu_memory(cpu) == {}
