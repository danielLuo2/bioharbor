# BioHarbor README demo. Render from the repo root with:  vhs docs/demo/demo.tape
#
# Needs BioHarbor + MMseqs2 + the swissprot database, and for the last step a GPU with
# `bioharbor[esmfold]` (see docs/gpu-setup.md). Waiting time of long-running steps is cut
# from the GIF with Hide/Show; everything shown is real output.

Output docs/demo/demo.gif

Require bioharbor
Require jq

Set Shell "bash"
Set FontSize 18
Set Width 1300
Set Height 680
Set Padding 28
Set Theme "Catppuccin Mocha"
Set TypingSpeed 40ms
Set WindowBar Colorful

Hide
Type "cd docs/demo && export PS1='$ ' && clear"
Enter
Sleep 500ms
Show

Type "# Bioinformatics tools an AI agent can run (over MCP). Here: the same tools from the CLI."
Enter
Sleep 500ms
Type "bioharbor tools list"
Enter
Sleep 3500ms

Type "# 1. Find genes in an assembled contig"
Enter
Sleep 400ms
Type "bioharbor run find_orfs sequence=@contig.fa --brief"
Enter
Wait+Screen@60s /file: jobs/
Sleep 3s

Hide
Type "PROT=$(bioharbor run find_orfs sequence=@contig.fa | jq -r '.result.summary.top[0].protein') && clear"
Enter
Sleep 6s
Show

Type "# 2. What is this protein? Homology search against Swiss-Prot (MMseqs2)"
Enter
Sleep 400ms
Type "bioharbor run search_homologs sequence=$PROT --brief"
Enter
Sleep 1s
Hide
Wait+Screen@600s /hits.tsv/
Show
Sleep 4s

# --- GPU step ---------------------------------------------------------------------------
Type "# 3. Predict its structure with ESMFold on the least busy GPU (shared 2x RTX 5090)"
Enter
Sleep 400ms
Type "bioharbor run predict_structure sequence=$PROT --brief"
Enter
Sleep 1s
Hide
Wait+Screen@900s /\.pdb/
Show
Sleep 6s
