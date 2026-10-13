# Security guards

`resolve_image_urls()` only touches `http(s)://` URLs -- `s3://` and
`data:` URIs pass through untouched. Every `http(s)://` URL goes through
the following guards, in `bridge/core.py`, before its bytes are ever
handed to a model.

## HTTPS-only by default

Plain `http://` is rejected unless you explicitly pass
`allow_insecure_http=True`. This exists so a caller can't accidentally
route image fetches over an unencrypted connection in production;
enabling it is meant for local testing against non-TLS hosts only.

## SSRF guard

Before connecting, the target hostname is resolved via
`socket.getaddrinfo()` and every returned IP is checked against the
classic SSRF target set: loopback (`127.0.0.1`), private ranges
(`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), link-local, and the
cloud instance metadata address (`169.254.169.254`). Any match raises
`ValueError` before a socket is opened.

Without this, an attacker-supplied `image_url` could make your server
fetch its own internal endpoints -- including, on EC2/ECS/Lambda, the
instance metadata service that can hand back IAM credentials.

## Bounded, re-validated redirects

Each request follows at most 2 redirect hops. The guard above is
re-applied to *every* redirect target, not just the original URL --
including resolving relative `Location` headers with `urljoin()` before
validating. A redirect chain can't be used to smuggle a request past the
SSRF check after the first hop passes.

## Size cap

Downloads are capped at `max_bytes` (default 20 MiB), enforced twice:
against the `Content-Length` response header (rejected before any body
bytes are read) and against the actual number of bytes streamed
(rejected mid-stream if a server lies about `Content-Length` or omits
it). This stops a malicious or misconfigured host from exhausting memory
with an unbounded or falsely-labeled response.

## Real format verification

Downloaded bytes are opened and verified with Pillow's
[`Image.verify()`](https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.Image.verify),
and the resulting `data:` URI's mime type comes from the verified image
format (`Image.MIME[fmt]`) -- never from the URL's file extension or a
server-supplied `Content-Type` header, both of which can be spoofed. Content
that isn't a real, decodable image raises `ValueError` and never reaches
the model call.

## What's explicitly out of scope

- **Content moderation / NSFW filtering** -- this guards against SSRF
  and resource-exhaustion attacks, not what the image actually depicts.
  Bedrock Guardrails or a dedicated moderation service is the right tool
  for that, applied to the resolved payload before or after this bridge.
- **Authentication on the fetched URL** -- if the image host requires
  auth (a signed URL, a bearer token), that's outside this function's
  concern; pass an already-authenticated URL, or a custom `session=`
  with the auth baked in via headers.

## Shared responsibility

This sample follows the [AWS Shared Responsibility
Model](https://aws.amazon.com/compliance/shared-responsibility-model/).
AWS is responsible for security **of** the cloud -- the Bedrock service,
its underlying infrastructure, and hardware. You are responsible for
security **in** the cloud once you copy this code into your own service,
including:

- The IAM role/policy your process runs under (least-privilege access
  to Bedrock and any other AWS APIs it calls).
- Network placement -- if this runs in a VPC, its security groups and
  egress rules are an additional layer alongside the SSRF guard above,
  not a replacement for it.
- Encryption in transit for anything you build around this bridge (the
  guards here already enforce HTTPS-only by default; see above).
- Monitoring, logging, and incident response for your deployment.

The guards documented above (SSRF blocklist, size cap, redirect
revalidation, format verification) are this sample's contribution to
*your* side of that line. They are a reference implementation for one
specific risk (fetching attacker-influenced URLs), not a complete
security posture -- review and harden this code for your own threat
model before running it against production traffic.
