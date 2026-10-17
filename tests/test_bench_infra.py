"""US-902 infra: `make bench` is the CPU harness check, and B4's work folder stays out of every
service.

The CPU against MI300X benchmarks were retired by ADR-18 (16 Oct 2026); their bench image,
compose file and GPU make targets, which no recorded run used, were removed on 17 Oct 2026. The
harness code in bench/ stays. Pure text and YAML parsing, as in tests/test_infra.py."""

from __future__ import annotations

import re

from tests.test_infra import COMPOSE_FILES, ROOT, bind_mounts, makefile_targets, services


def test_no_service_sees_the_bench_work_folder() -> None:
    """B4 writes a generated store, truth included until it ends, under bench-work/ (on the CPU
    too): no compose service mounts it or an ancestor of it (rule 12), and git ignores it."""
    work = ROOT / "bench-work"
    for name in COMPOSE_FILES:
        for service_name, service in services(name).items():
            for source, _, _ in bind_mounts(service):
                assert source != work and work not in source.parents, (name, service_name)
                assert source not in work.parents, (name, service_name, "mounts an ancestor")
    assert "bench-work/" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_make_bench_is_the_cpu_harness_check() -> None:
    targets = makefile_targets()
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert targets["bench"][0] == "bench-cpu"
    cpu = " ".join(targets["bench-cpu"][1])
    assert "python -m bench run --device cpu --run-id $(BENCH_RUN)" in cpu
    assert "python -m bench report" in cpu and "--out $(BENCH_DOC)" in cpu
    assert re.search(r"^BENCH_DOC \?= docs/results/benchmarks-cpu\.md$", makefile, re.M)
    assert re.search(r"^BENCH_RUN := .*-cpu$", makefile, re.M)
    # bench/env.py records the commit and the dirty flag from these (containers have no .git).
    assert re.search(r"^export BENCH_GIT_COMMIT \?= ", makefile, re.M)
    assert re.search(r"^export BENCH_GIT_DIRTY \?= ", makefile, re.M)
    gpu = {t for t in targets if re.fullmatch(r"bench-(gpu|b[1-4]|vllm.*|build|merge)", t)}
    assert not gpu, gpu  # the compose files the Makefile names: tests/test_infra.py
    phony = re.search(r"^\.PHONY:(.*)$", makefile.replace("\\\n", " "), re.M)
    assert phony is not None
    for target in ("bench", "bench-cpu"):
        assert re.search(rf"(^|\s){re.escape(target)}(\s|$)", phony.group(1)), target
