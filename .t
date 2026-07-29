apiVersion: templates.gatekeeper.sh/v1
kind: ConstraintTemplate
metadata:
  name: causaremediationallowlist
spec:
  crd:
    spec:
      names:
        kind: CausaRemediationAllowlist
      validation:
        openAPIV3Schema:
          type: object
          properties:
            allowedNamespaces:
              type: array
              items:
                type: string
            allowedConfigMaps:
              type: array
              items:
                type: string
  targets:
    - target: admission.k8s.gatekeeper.sh
      rego: |
        package causaremediationallowlist
        violation[{"msg": msg}] {
          input.review.operation == "UPDATE"
          input.review.object.kind == "ConfigMap"
          name := input.review.object.metadata.name
          ns := input.review.object.metadata.namespace
          allowed_ns := {x | x := input.parameters.allowedNamespaces[_]}
          count(allowed_ns) > 0
          not allowed_ns[ns]
          msg := sprintf("Causa remediation blocked ConfigMap %v/%v: namespace not allowlisted", [ns, name])
        }
        violation[{"msg": msg}] {
          input.review.operation == "UPDATE"
          input.review.object.kind == "ConfigMap"
          name := input.review.object.metadata.name
          allowed := {x | x := input.parameters.allowedConfigMaps[_]}
          count(allowed) > 0
          not allowed[name]
          startswith(input.review.userInfo.username, "system:serviceaccount:causa-remediation:")
          msg := sprintf("worker may only mutate allowlisted ConfigMaps; got %v", [name])
        }
