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

## 4. Calibrate the memory estimate

Each run's `provenance.json` records `gpu_mem_estimate_gb` (what the scheduler reserved)
and `gpu_peak_mem_gb` (what PyTorch actually allocated). Fold a few lengths and compare:

```bash
for n in 100 300 600 1000; do
  bioharbor run predict_structure sequence=$(python -c "print(('MKTAYIAKQRQISFVKSHFSRQ' * 100)[:$n])")
done
grep -h '"gpu_' ~/.bioharbor/jobs/*/provenance.json
```

If the estimate is far above the peak, BioHarbor is leaving GPU memory unused; if it is
below, jobs risk OOM. Please share the numbers in an issue so the default
(`estimate_gpu_gb` in `tools/structure.py`) can be tuned for everyone.

## 5. Homology search

```bash
bioharbor setup-db swissprot   # ~0.5 GB download from UniProt
bioharbor run search_homologs sequence=MVLSPADKTNVKAAWGKVGAHAGEYGAEALERMF
```

Behind a restrictive network, download `uniprot_sprot.fasta.gz` yourself and run
`bioharbor setup-db swissprot --from-fasta uniprot_sprot.fasta.gz`.
