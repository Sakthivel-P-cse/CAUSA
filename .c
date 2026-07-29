apiVersion: constraints.gatekeeper.sh/v1beta1
kind: CausaRemediationAllowlist
metadata:
  name: causa-remediation-allowlist
spec:
  match:
    kinds:
      - apiGroups: [""]
        kinds: ["ConfigMap"]
    namespaces: ["otel-demo"]
  parameters:
    allowedNamespaces: ["otel-demo"]
    allowedConfigMaps: ["flagd-config"]
