# Kubernetes deployment example

These manifests demonstrate a cloud-neutral Kubernetes shape for the LCP
reference platform. They are intentionally not a complete turnkey production
cluster configuration.

## Before applying

1. Build and publish the reference-platform image to your registry.
2. Provide a supported Postgres service; do not run the database in this
   example for production.
3. Copy `secret.example.yaml` to a private, access-controlled Secret manifest
   or use External Secrets/KMS integration.
4. Replace the example image, database URL, tenant ID, resource limits,
   namespaces, network policies, ingress, and residency controls.
5. Configure a production WSGI server or adapt the image entry point to the
   organization's process manager.
6. Add a TLS ingress/WAF and private egress policy.

## Apply

```bash
kubectl create namespace lcp
kubectl apply -n lcp -f secret.example.yaml   # replace with a real secret workflow
kubectl apply -n lcp -f configmap.yaml
kubectl apply -n lcp -f deployment.yaml
kubectl apply -n lcp -f service.yaml
kubectl apply -n lcp -f pod-disruption-budget.yaml
# Optional; tune against queue age and database capacity before applying.
kubectl apply -n lcp -f hpa.example.yaml
```

The API and worker are separate deployments. Both use the same image and
configuration; only the entry point differs. The worker needs access to the
same Postgres database but does not need a public Service.

## Production additions

- `network-policy.example.yaml` restricting database and buyer egress.
- `pod-disruption-budget.yaml` and multi-zone scheduling.
- `hpa.example.yaml`, extended with queue-age and database-capacity signals.
- External Secrets/KMS and key rotation, including controlled re-encryption of persisted envelopes.
- Ingress TLS, WAF, DDoS protection, and request-size limits.
- Postgres HA, backups, encryption, and restore drills.
- Centralized redacted logs, metrics, traces, and alerts.
- Image signing, admission policy, SBOM verification, and vulnerability scans.
