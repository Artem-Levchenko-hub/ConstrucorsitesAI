FROM tobix/pywine:3.12
# Windows-Python tooling under Wine
RUN wine python -m pip install --no-cache-dir "pyinstaller>=6.3.0"
# NSIS runs natively on Linux (no Wine needed to wrap the payload). python3 is the
# native Linux interpreter that runs build.py (the tobix/pywine base ships only the
# Windows Python under Wine — there is no native python3 on PATH without this).
RUN apt-get update && apt-get install -y --no-install-recommends nsis python3 \
    && rm -rf /var/lib/apt/lists/*
# The Wine prefix was created as root by the pip step above; Wine refuses a prefix
# "not owned by you", so hand it (and everything pip wrote into it) to builder.
RUN useradd -m builder && chown -R builder:builder /opt/wineprefix
COPY build.py /usr/local/bin/build.py
USER builder
WORKDIR /work
ENTRYPOINT ["python3", "/usr/local/bin/build.py"]
