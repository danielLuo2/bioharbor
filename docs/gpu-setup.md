# GPU setup & validation

This guide gets `predict_structure` (ESMFold) running on an NVIDIA GPU and checks that
BioHarbor's scheduling behaves on *your* hardware. Written against an RTX 5090 (32 GB,
Blackwell); other cards work the same way.

## 1. Install

RTX 50xx (Blackwell, sm_120) needs a PyTorch build for **CUDA 12.8 or newer**. Install
torch first, then BioHarbor with the ESMFold extra:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cu128
pip install "bioharbor[esmfold]"
bioharbor doctor
```

> PyPI mirrors can lag behind: if you see `No matching distribution found for mcp>=2.3`,
> install with `-i https://pypi.org/simple`.

`doctor` should show both GPUs with their free memory and `esmfold ... CUDA available`.

The first prediction downloads the ESMFold weights (~8 GB) from the Hugging Face Hub
into `~/.cache/huggingface`. Set `HF_HOME` to put them elsewhere, or `HF_ENDPOINT` to use
a mirror if the Hub is slow from your network.

## 2. Smoke test

```bash
bioharbor run predict_structure \
  sequence=MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG
```

Ubiquitin is a well-folded protein: expect `mean_plddt` around 85–95 and a
`seq1.pdb` file under `~/.bioharbor/jobs/<job_id>/`. Open it in PyMOL/ChimeraX to check.

While loading, transformers reports `esm.contact_head.regression.{weight,bias} | MISSING`.
This is expected: the ESMFold checkpoint does not ship ESM-2's contact-prediction head,
which folding never uses.

**Each `bioharbor run` is a new process and reloads the ~8 GB model** (the reference
RTX 5090 run took ~160 s end-to-end for a 76-aa protein, most likely dominated by loading). Under
`bioharbor serve` the model stays loaded, so an agent's later predictions only pay the
inference time. `provenance.json` splits the two: `model_load_s` and `inference_s`.

## 3. Check placement on a shared machine

While a labmate's job occupies one GPU, run the smoke test again and confirm:

- `bioharbor jobs` / the result's `gpu` field shows the **less busy** GPU was chosen;
- `nvidia-smi` shows the BioHarbor process on that same GPU index (BioHarbor sets
  `CUDA_DEVICE_ORDER=PCI_BUS_ID` so NVML and CUDA agree on numbering).

If neither GPU has room, the job shows `waiting_gpu` and starts once memory frees up.

## 4. GPU memory per sequence length

Measured on an RTX 5090 (torch 2.11 + CUDA 12.8, transformers 5.18) with BioHarbor's
settings: fp16 language model, trunk chunking, `expandable_segments`. "Peak" is the
whole process as `nvidia-smi` shows it (weights, CUDA context and PyTorch's cache),
which is what the GPU must have free. Times exclude the one-time model load (~155 s).

| Length (aa) | Peak (GB) | Estimate (GB) | Inference (s) |
|---:|---:|---:|---:|
| 50 | 8.5 | 9.0 | 0.6 |
| 300 | 9.2 | 9.7 | 2.1 |
| 500 | 10.6 | 11.1 | 8.1 |
| 800 | 13.7 | 14.2 | 27.3 |
| 1000 | 16.6 | 17.2 | 52.2 |
| 1200 | 20.1 | 20.8 | 86.9 |
| 1500 | 26.6 | 27.4 | 175.6 |

- Peaks follow 8.5 + 8.1·(L/1000)² GB; the scheduler's estimate
  (`estimate_gpu_gb` in `tools/structure.py`) adds ~0.5 GB, plus 1 GB headroom per GPU.
- `num_recycles` changes time, not memory: 1000 aa with 12 recycles peaks at the same
  memory as with 4 and takes ~3× longer.
- Once loaded, the model keeps ~8.5 GB on its GPU between jobs; later jobs on that GPU
  only need the activation part. BioHarbor empties PyTorch's cache after every fold.
- Without chunking and `expandable_segments`, 600 aa peaked at 17.4 GB and 1500 aa at
  30.7 GB — barely inside a 32 GB card.

To check your own hardware, compare `gpu_mem_estimate_gb` with `gpu_peak_reserved_gb`
(+~0.6 GB CUDA context) in each run's `provenance.json`:

```bash
for n in 100 300 600 1000; do
  bioharbor run predict_structure sequence=$(python -c "print(('MKTAYIAKQRQISFVKSHFSRQ' * 100)[:$n])")
done
grep -h '"gpu_' ~/.bioharbor/jobs/*/provenance.json
```

If the estimate is below the peak, jobs risk OOM; please share the numbers in an issue.

## 5. Homology search

```bash
bioharbor setup-db swissprot   # ~0.5 GB download from UniProt
bioharbor run search_homologs sequence=MVLSPADKTNVKAAWGKVGAHAGEYGAEALERMF
```

Behind a restrictive network, download `uniprot_sprot.fasta.gz` yourself and run
`bioharbor setup-db swissprot --from-fasta uniprot_sprot.fasta.gz`.
