spec:
  replicas: ${REPLICAS}
  template:
    spec:
      nodeSelector:
        kubernetes.io/hostname: ${NODE_NAME}
