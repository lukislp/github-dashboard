#!/usr/bin/env bash
# Produces k8s/02-sealed-secret.yaml for THIS cluster from the GitHub OAuth App credentials.
# Values are read hidden from the terminal and never echoed; SECRET_KEY is generated here.
# The output is a Bitnami SealedSecret encrypted with the cluster controller's public key, so
# it is safe to commit to this public repository - only that controller can decrypt it.
#
#   bash k8s/seal-secret.sh                # fetches the cert from the cluster (needs kubeconfig)
#   SEALED_SECRETS_CERT=cert.pem bash ...  # or use a previously fetched controller certificate
#
# Apply afterwards (bootstrap-only, not Flux-managed): kubectl apply -f k8s/02-sealed-secret.yaml
# Requires: kubectl, kubeseal (https://github.com/bitnami-labs/sealed-secrets), python 3.
set -euo pipefail

NAMESPACE="github-dashboard"
NAME="github-dashboard-secrets"
OUT="$(dirname "$0")/02-sealed-secret.yaml"
CONTROLLER_NS="${SEALED_SECRETS_CONTROLLER_NAMESPACE:-kube-system}"
CONTROLLER_NAME="${SEALED_SECRETS_CONTROLLER_NAME:-sealed-secrets-controller}"

read -r -p "GITHUB_CLIENT_ID: " CLIENT_ID
[ -n "$CLIENT_ID" ] || { echo "empty client id" >&2; exit 1; }
read -r -s -p "GITHUB_CLIENT_SECRET (hidden): " CLIENT_SECRET; echo
[ -n "$CLIENT_SECRET" ] || { echo "empty client secret" >&2; exit 1; }
PY="$(command -v python3 || command -v python)"
SECRET_KEY="$("$PY" -c 'import secrets; print(secrets.token_urlsafe(48))')"

CERT_ARGS=()
if [ -n "${SEALED_SECRETS_CERT:-}" ]; then
  CERT_ARGS=(--cert "$SEALED_SECRETS_CERT")
else
  CERT_ARGS=(--controller-namespace "$CONTROLLER_NS" --controller-name "$CONTROLLER_NAME")
fi

kubectl create secret generic "$NAME" --namespace "$NAMESPACE" --dry-run=client -o yaml \
  --from-literal=GITHUB_CLIENT_ID="$CLIENT_ID" \
  --from-literal=GITHUB_CLIENT_SECRET="$CLIENT_SECRET" \
  --from-literal=SECRET_KEY="$SECRET_KEY" \
  | kubeseal "${CERT_ARGS[@]}" --format yaml > "$OUT"

unset CLIENT_SECRET SECRET_KEY
echo "wrote $OUT ($(grep -c ': Ag' "$OUT") sealed values). Commit it, then: kubectl apply -f $OUT"
