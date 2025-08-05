FROM python:3.9
# 3.10 (latest) and 3.11 (edge) don't work
# https://github.com/libfuse/pyfuse3/issues/52

WORKDIR /usr/src/app

RUN apt-get update && apt-get install -y \
  libfuse3-dev \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV JOPLINFS_MOUNT=/mnt
CMD [ "python", "./src/filesystem.py" ]
