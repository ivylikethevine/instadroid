#!/bin/bash -eu
# Run by OSS-Fuzz's `compile` inside .clusterfuzzlite/Dockerfile's image: packages each fuzzer in
# tests/fuzz/ with PyInstaller into $OUT, with the profile fixtures as its seed corpus.
export PATH="/opt/python/bin:$PATH"
fuzzer=""
for fuzzer in tests/fuzz/fuzz_*.py; do
  # What PyInstaller can't see by following imports: instadroid's data files (crash_signatures.tsv,
  # tune_packages.txt), and igprofiles, which finds its profiles by listing its own directory and
  # imports them by name, so it goes in as data too and its modules as hidden imports.
  compile_python_fuzzer "$fuzzer" --paths app \
    --add-data app/instadroid/crash_signatures.tsv:instadroid \
    --add-data app/instadroid/tune_packages.txt:instadroid \
    --add-data app/igprofiles:igprofiles --collect-submodules igprofiles
  zip -qj "$OUT/$(basename -s .py "$fuzzer")_seed_corpus.zip" app/igprofiles/*/fixtures/*.xml
done
