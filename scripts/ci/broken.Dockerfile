# The rollback check (R6-D04): the good image, except that `saley web` exits at once.
# deploy.sh --image saley:broken must find web unhealthy, put the previous image back, and say
# "ROLLED BACK". Built in CI only, never on the server.
FROM saley:current
USER root
RUN printf '#!/bin/sh\nif [ "$2" = web ]; then echo "broken web (rollback check)"; exit 1; fi\nexec "$@"\n' \
    > /usr/local/bin/broken-web && chmod 755 /usr/local/bin/broken-web
USER saley
ENTRYPOINT ["/usr/local/bin/broken-web"]
