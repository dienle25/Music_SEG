# Paper

`main.tex` is the manuscript (English). Numbers, tables and the figure in `generated/` are computed from the
result files of a work folder; never edit them by hand.

    python3 scripts/collection_stats.py work_pilot          # corpus and query statistics
    python3 scripts/make_paper_tables.py --work work_pilot --out paper/generated
    cd paper && pdflatex main && bibtex main && pdflatex main && pdflatex main

Red bracketed text marks results that are not available yet (GPU run, full catalogue, human queries);
blue bracketed text marks information only the authors can supply. When new results arrive (for example
`visongsearch_results.zip` from the Kaggle notebook, unzipped into the work folder), re-run the two
scripts, recompile, and then revise the sentences of the Results section: they describe the current data.
