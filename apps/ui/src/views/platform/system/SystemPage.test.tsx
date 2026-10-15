// System and benchmarks on the contract examples (US-901, US-902, US-905; docs/ui-design.md 9.4).
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import { ApiError } from '@/api/errors';
import type { ReadyzResponse, TelemetryResponse } from '@/api/models';
import { createQueryClient } from '@/api/queries';
import { setScenario } from '@/api/scenario';
import { readExample } from '@/test/examples';
import { renderApp } from '@/test/render';
import { findBannedWording, findUnprovenancedNumbers } from '@/test/scan';
import { GpuDock } from './GpuDock';

afterEach(() => {
  vi.restoreAllMocks();
  act(() => setScenario('default'));
});

async function loaded() {
  renderApp('/system');
  await waitFor(() => expect(document.querySelectorAll('[data-benchmark]')).toHaveLength(4));
  await waitFor(() => expect(document.querySelector('[data-readiness]')).not.toBeNull());
}

describe('benchmarks (US-902)', () => {
  it('labels each benchmark Measured, Pending or Illustrative, not measured', async () => {
    await loaded();
    const status = (id: string) => document.querySelector(`[data-benchmark="${id}"] [data-benchmark-status]`)?.getAttribute('data-benchmark-status');
    expect(['B1', 'B2', 'B3', 'B4'].map(status)).toEqual(['illustrative', 'pending', 'illustrative', 'pending']);
    expect(document.querySelector('[data-benchmark="B1"]')?.textContent).toContain('Illustrative, not measured');
    expect(document.querySelector('[data-benchmark="B1"] [data-watermark]')).not.toBeNull();
    expect(document.querySelector('[data-benchmark="B2"]')?.textContent).toContain('Not run yet');
    expect(document.querySelector('[data-benchmark="B2"]')?.textContent).toContain('not evidence on account links');
  });

  it('pairs CPU and MI300X per metric, one chart per phase and unit, with an illustrative mark on every figure', async () => {
    await loaded();
    const b1 = document.querySelector('[data-benchmark="B1"]') as HTMLElement;
    expect(Array.from(b1.querySelectorAll('[data-device]')).map((d) => d.getAttribute('data-device'))).toEqual(['cpu', 'mi300x']);
    expect(Array.from(b1.querySelectorAll('[data-figure]')).every((f) => f.getAttribute('data-nature') === 'illustrative')).toBe(true);
    const b3 = document.querySelector('[data-benchmark="B3"]') as HTMLElement;
    expect(Array.from(b3.querySelectorAll('[data-chart]')).map((c) => c.getAttribute('data-chart'))).toEqual([
      'generation:milliseconds',
      'generation:tokens_per_second',
      'cost:eur',
    ]);
    expect(b3.querySelector('[data-metric="latency_p50"]')?.textContent).toContain('concurrency 1');
    expect(b3.querySelector('[data-metric="throughput"]')?.textContent).toContain('batch 32');
    expect(b1.querySelector('[data-metric="train_time"]')?.textContent).toContain('decision-time protocol');
  });

  it('shows no speedup between illustrative results', async () => {
    await loaded();
    expect(document.querySelector('[data-speedups]')).toBeNull();
  });

  it('leads with the cost per decision, idle included, marked illustrative', async () => {
    await loaded();
    const tile = document.querySelector('[data-cost-per-decision]') as HTMLElement;
    expect(tile.getAttribute('data-cost-per-decision')).toBe('illustrative');
    expect(tile.textContent).toContain('€0.01');
    expect(tile.textContent).toContain('Cost per decision, idle included');
    expect(tile.textContent).toContain('B3');
  });

  it('keeps dataset, versions and configuration one disclosure away', async () => {
    await loaded();
    const details = document.querySelector('[data-benchmark="B1"] [data-benchmark-details]') as HTMLDetailsElement;
    expect(details.textContent).toContain('ASOS GraphReturns train split');
    expect(details.textContent).toContain('rocm 7.2.3');
    expect(details.textContent).toContain('max_depth=4');
  });
});

describe('service mode and readiness (US-901)', () => {
  it('reads /healthz, /readyz and the mode header', async () => {
    await loaded();
    const facts = document.querySelector('[data-mode-facts]') as HTMLElement;
    // On the mock adapter every fact is labelled as the /readyz contract example (UI12).
    await waitFor(() => expect(facts.textContent).toContain('ok · the mock adapter answers (no API)'));
    expect(facts.querySelector('[data-mode]')?.textContent).toBe('contract example: Live');
    expect(facts.textContent).toContain('Gemma 4 on vLLM');
    expect(facts.querySelector('[data-header-agreement]')?.getAttribute('data-header-agreement')).toBe('agrees');
    expect(facts.textContent).toContain('contract examples (mock adapter)');
    const checks = Array.from(document.querySelectorAll('[data-readiness] [data-component]')).map((c) => [
      c.getAttribute('data-component'),
      c.getAttribute('data-check'),
    ]);
    expect(checks).toContainEqual(['vllm', 'ok']);
    expect(checks).toContainEqual(['demo_cache', 'disabled']);
    expect(document.querySelector('[data-versions]')?.textContent).toContain('policy-2026-10-12');
    expect(screen.queryAllByText('Live · ready')).toEqual([]);
    expect(document.querySelector('[data-readiness-pill]')?.getAttribute('data-readiness-pill')).toBe('contract');
    expect(document.querySelector('[data-readiness-pill]')?.textContent).toBe('Contract examples · /readyz example: Live');
    expect(document.querySelector('[data-contract-example-note]')).not.toBeNull();
  });

  it('says cached in the cached scenario, with the label and the GPU off', async () => {
    act(() => setScenario('cached'));
    await loaded();
    const facts = document.querySelector('[data-mode-facts]') as HTMLElement;
    expect(facts.querySelector('[data-mode]')?.textContent).toBe('contract example: Cached');
    expect(facts.textContent).toContain('Precomputed on AMD MI300X');
    expect(document.querySelector('[data-component="vllm"]')?.textContent).toContain('GPU off in cached mode');
  });
});

describe('the service clock (ADR-15 sections 15, 19, 20)', () => {
  it('shows the kind, the now, the snapshot instant, the reset in wall time and the note', async () => {
    await loaded();
    const clock = await waitFor(() => {
      const el = document.querySelector('[data-clock]');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(clock.getAttribute('data-clock')).toBe('demo');
    expect(clock.getAttribute('data-clock-frozen')).toBe('no');
    expect(clock.textContent).toContain('contract example: demo clock');
    expect(clock.textContent).toContain('snapshot 30 Sep 2026, 18:00 UTC');
    expect(clock.textContent).toContain('(wall time)');
    expect(clock.querySelector('[data-verbatim]')?.textContent).toMatch(/^Demo clock/);
  });

  it('says a frozen clock is frozen and never moves it (the read-only demo)', async () => {
    act(() => setScenario('cached'));
    await loaded();
    const clock = await waitFor(() => {
      const el = document.querySelector('[data-clock][data-clock-frozen="yes"]');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(clock.querySelector('[data-clock-state]')?.textContent).toBe('frozen at the snapshot instant: it does not move');
    expect(clock.textContent).toContain('now 30 Sep 2026, 18:00 UTC');
    expect(clock.textContent).toContain('the read-only demo');
  });

  it('says a frozen clock past its snapshot stands at the latest recorded act (contract 1.8.1)', async () => {
    const cached = readExample<ReadyzResponse>('readyz_response.cached.json');
    const note = 'Demo clock, frozen at the latest act recorded after the serving snapshot: the read-only demo';
    vi.spyOn(api, 'readyz').mockResolvedValue({ ...cached, clock: { ...cached.clock, now: '2026-09-30T19:12:00Z', note } });
    act(() => setScenario('cached'));
    await loaded();
    const clock = await waitFor(() => {
      const el = document.querySelector('[data-clock][data-clock-frozen="yes"]');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(clock.querySelector('[data-clock-state]')?.textContent).toBe(
      'frozen at the latest act recorded after the snapshot: it does not move',
    );
    expect(clock.textContent).toContain('now 30 Sep 2026, 19:12 UTC');
    expect(clock.textContent).toContain('snapshot 30 Sep 2026, 18:00 UTC');
    expect(clock.querySelector('[data-verbatim]')?.textContent).toBe(note);
  });
});

describe('telemetry (US-905)', () => {
  it('labels the sample times as wall time', async () => {
    await loaded();
    const panel = document.querySelector('#telemetry') as HTMLElement;
    await waitFor(() => expect(panel.querySelector('[data-wall-time]')).not.toBeNull());
    expect(panel.textContent).toContain('GPU samples keep the real clock, not the demo clock');
    expect(panel.querySelector('[data-telemetry-history] th')?.textContent).toBe('Time (UTC, wall time)');
  });

  it('shows the note of a live service without a GPU sample as such, not as the cached mode', async () => {
    const cpu: TelemetryResponse = {
      mode: 'live',
      sampled_at: '2026-10-15T00:36:35Z',
      interval_s: 5,
      device: null,
      gpu: null,
      gpu_history: [],
      llm: null,
      api_latency_p95: null,
      note: 'No GPU sample in the last 60 s: the amd-smi sampler is not running (DEVICE=cpu)',
    };
    vi.spyOn(api, 'getTelemetry').mockResolvedValue(cpu);
    await loaded();
    const panel = document.querySelector('#telemetry') as HTMLElement;
    const note = await waitFor(() => {
      const el = panel.querySelector('[data-telemetry-no-sample]');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(note.textContent).toContain('No GPU sample to show');
    expect(note.textContent).toContain('the amd-smi sampler is not running');
    expect(panel.querySelector('[data-telemetry-cached]')).toBeNull();
    expect(panel.querySelectorAll('[data-figure]')).toHaveLength(0);
    expect(findUnprovenancedNumbers(panel)).toEqual([]);
  });

  it('says how to sign in when the proxy protects the telemetry (401), never a page that does not exist', async () => {
    vi.spyOn(api, 'getTelemetry').mockRejectedValue(new ApiError({ status: 401, code: 'protected', message: 'protected' }));
    await loaded();
    const panel = document.querySelector('#telemetry') as HTMLElement;
    await waitFor(() => expect(panel.querySelector('[data-telemetry-protected]')).not.toBeNull());
    const text = panel.querySelector('[data-telemetry-protected]')?.textContent ?? '';
    expect(text).toContain('basic auth');
    expect(text).toContain('/api/telemetry');
    expect(text).not.toContain('/admin');
  });

  it('shows GPU, memory, power, the language model and the API, marked illustrative', async () => {
    await loaded();
    const panel = document.querySelector('#telemetry') as HTMLElement;
    await waitFor(() => expect(panel.querySelector('[data-telemetry-illustrative]')).not.toBeNull());
    expect(panel.textContent).toContain('71%');
    expect(panel.textContent).toContain('143.0 GiB');
    expect(panel.textContent).toContain('192.0 GiB');
    expect(panel.textContent).toContain('535 W');
    expect(panel.textContent).toContain('1,450 tok/s');
    expect(panel.textContent).toContain('84 ms');
    expect(panel.querySelectorAll('[data-sparkline]').length).toBeGreaterThan(0);
  });

  it('shows the note and no number in cached mode, never zeros', async () => {
    act(() => setScenario('cached'));
    await loaded();
    const panel = document.querySelector('#telemetry') as HTMLElement;
    await waitFor(() => expect(panel.querySelector('[data-telemetry-cached]')).not.toBeNull());
    // The cached note names no hardware (contract 1.5.2): where outputs were made is readyz.label's claim.
    expect(panel.textContent).toContain('GPU off: cached mode serves precomputed results');
    expect(panel.textContent).not.toContain('MI300X');
    expect(panel.querySelectorAll('[data-figure]')).toHaveLength(0);
  });
});

describe('the page as a whole', () => {
  it.each(['default', 'cached'])('%s: every number has a provenance and the wording is clean', async (scenario) => {
    act(() => setScenario(scenario));
    await loaded();
    await waitFor(() => expect(document.querySelector('#telemetry [data-telemetry-illustrative], #telemetry [data-telemetry-cached]')).not.toBeNull());
    expect(findUnprovenancedNumbers(document.body)).toEqual([]);
    expect(findBannedWording(document.body).errors).toEqual([]);
  });
});

describe('GpuDock', () => {
  function renderDock(open: boolean) {
    const client = createQueryClient();
    client.setDefaultOptions({ queries: { retry: false, refetchInterval: false } });
    let closed = false;
    render(
      <QueryClientProvider client={client}>
        <GpuDock open={open} onClose={() => (closed = true)} />
      </QueryClientProvider>,
    );
    return { closed: () => closed };
  }

  it('shows utilisation, memory and tokens per second, and closes', async () => {
    const dock = renderDock(true);
    const aside = await screen.findByRole('complementary', { name: 'GPU' });
    await waitFor(() => expect(aside.textContent).toContain('71%'));
    expect(aside.textContent).toContain('1,450 tok/s');
    fireEvent.click(within(aside).getByRole('button', { name: 'Close the GPU dock' }));
    expect(dock.closed()).toBe(true);
  });

  it('shows the cached note instead of numbers', async () => {
    act(() => setScenario('cached'));
    renderDock(true);
    const aside = await screen.findByRole('complementary', { name: 'GPU' });
    await waitFor(() => expect(aside.textContent).toContain('GPU off'));
    expect(aside.querySelectorAll('[data-figure]')).toHaveLength(0);
  });

  it('renders nothing while closed', () => {
    renderDock(false);
    expect(screen.queryByRole('complementary', { name: 'GPU' })).toBeNull();
  });
});
