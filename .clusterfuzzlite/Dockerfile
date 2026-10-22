# The ClusterFuzzLite build image (.github/workflows/cflite.yml). OSS-Fuzz's Python builder, pinned by
# digest; the tag says which image it is.
FROM gcr.io/oss-fuzz-base/base-builder-python@sha256:21f6fab17ace4dc76e1c56de91ee95d3d03da021df94b41056ba036f6c6f55e9
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
# The builder's Python is 3.11 and the app needs 3.14, so a portable CPython (python-build-standalone,
# built for an old glibc) goes in /opt/python, pinned to a release and checksum. Only build.sh puts it on
# PATH: OSS-Fuzz's `compile` still takes the sanitizer runtime from the builder's own atheris.
ADD --checksum=sha256:dfa4c5a40c47f338f41658017be550c72247c391e6bfb1fa4da03ebc8451abdf \
    https://github.com/astral-sh/python-build-standalone/releases/download/20261021/cpython-3.14.9%2B20261021-x86_64-unknown-linux-gnu-install_only.tar.gz \
    /tmp/python.tar.gz
RUN tar -xzf /tmp/python.tar.gz -C /opt && rm /tmp/python.tar.gz
COPY app/requirements.txt /tmp/app-requirements.txt
COPY .clusterfuzzlite/requirements.txt /tmp/fuzz-requirements.txt
RUN /opt/python/bin/python3 -m pip install --no-cache-dir --require-hashes \
    -r /tmp/app-requirements.txt -r /tmp/fuzz-requirements.txt \
    && rm /tmp/*-requirements.txt
COPY . $SRC/instadroid
WORKDIR $SRC/instadroid
COPY .clusterfuzzlite/build.sh $SRC/
