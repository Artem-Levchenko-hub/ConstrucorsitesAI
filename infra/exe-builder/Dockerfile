FROM tobix/pywine:3.12
# Windows-Python tooling under Wine
RUN wine python -m pip install --no-cache-dir "pyinstaller>=6.3.0"
# NSIS runs natively on Linux (no Wine needed to wrap the payload)
RUN apt-get update && apt-get install -y --no-install-recommends nsis \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -m builder
COPY build.py /usr/local/bin/build.py
USER builder
WORKDIR /work
ENTRYPOINT ["python3", "/usr/local/bin/build.py"]
