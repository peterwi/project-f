FROM docker:27-cli

RUN apk add --no-cache bash ca-certificates coreutils tzdata supercronic make python3 py3-yaml git

WORKDIR /repo

COPY docker/scheduler/crontab /etc/crontab
COPY docker/scheduler/job.sh /usr/local/bin/job.sh
RUN chmod +x /usr/local/bin/job.sh

ENV TZ=Europe/London

ENTRYPOINT ["supercronic", "-no-reap", "/etc/crontab"]
