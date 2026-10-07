"""Coverage-guided fuzzing of the pure hierarchy parsers (atheris, libFuzzer's engine). A finding is any
exception other than lxml's syntax error on input that isn't XML.

    python tests/fuzz/fuzz_parsing.py -max_total_time=60 local/fuzz-corpus app/igprofiles/v424/fixtures

libFuzzer writes the inputs it keeps into the first directory, so the fixtures go second, as seeds only."""

import contextlib
import sys

import atheris
from lxml import etree

with atheris.instrument_imports():
    from instadroid import parsing


def fuzz_one(data: bytes) -> None:
    xml: str = data.decode("utf-8", "replace")
    with contextlib.suppress(etree.XMLSyntaxError):
        parsing.parse_screen(xml)


if __name__ == "__main__":
    atheris.Setup(sys.argv, fuzz_one)
    atheris.Fuzz()
