# Docker build context

Run `npm run prepare:claude -- 2.1.195` before building or deploying. The script downloads the pinned native Linux `claude` binary, verifies the signed release manifest, checks the binary checksum, and writes `docker/claude`.

`docker/claude` is intentionally ignored by git because it is a large release artifact.
