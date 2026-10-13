# PromQL for the shop (paste into Omni › Explore › Metrics)

Metrics are PromQL only, never SQL. `traces.span.metrics.*` comes from `cloudwatch-plugin-otel`. The `@resource.*` labels identify the service. Dotted or `@` label names must be double-quoted.

The plugin meters every span (server, client, and internal) and labels each datapoint with `span.kind` (`SERVER`/`CLIENT`/`INTERNAL`), `status.code` (`ERROR`/`UNSET`/`OK`), `span.name`, and `http.route`. Filter on `"span.kind"="SERVER"` for request-level RED.

```promql
# Request rate per service (calls/s)
sum by ("@resource.service.name") (rate({__name__="traces.span.metrics.calls", "@resource.service.namespace"="shop", "span.kind"="SERVER"}[5m]))

# p99 latency per service (seconds). Use the base histogram name: no _bucket, no by (le).
histogram_quantile(0.99, sum by ("@resource.service.name") (rate({__name__="traces.span.metrics.duration", "@resource.service.namespace"="shop", "span.kind"="SERVER"}[5m])))

# Error ratio for payments' inbound requests
sum(rate({__name__="traces.span.metrics.calls", "@resource.service.name"="payments", "span.kind"="SERVER", "status.code"="ERROR"}[5m]))
  / sum(rate({__name__="traces.span.metrics.calls", "@resource.service.name"="payments", "span.kind"="SERVER"}[5m]))

# Custom business metrics emitted by the services
sum(rate({__name__="orders.placed"}[5m]))
sum by (outcome) (rate({__name__="payments.charges"}[5m]))
```
