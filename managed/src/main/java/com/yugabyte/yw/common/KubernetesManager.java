// Copyright (c) YugaByte, Inc.

package com.yugabyte.yw.common;

import com.google.common.annotations.VisibleForTesting;
import com.google.common.collect.ImmutableList;
import com.google.inject.Inject;
import com.yugabyte.yw.models.Provider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import javax.inject.Singleton;
import java.util.List;
import java.util.Map;
import java.util.UUID;

@Singleton
public class KubernetesManager {
  public static final Logger LOG = LoggerFactory.getLogger(KubernetesManager.class);

  private static final long DEFAULT_TIMEOUT_SECS = 300;

  @Inject
  ShellProcessHandler shellProcessHandler;

  @Inject
  play.Configuration appConfig;

  private static String SERVICE_INFO_JSONPATH="{.spec.clusterIP}|{.status.*.ingress[0].ip}|{.status.*.ingress[0].hostname}";

  @VisibleForTesting
  static String POSTGRES_BIN_PATH = "/home/yugabyte/tserver/postgres/bin";

  public ShellProcessHandler.ShellResponse createNamespace(Map<String, String> config, String universePrefix) {
    List<String> commandList = ImmutableList.of("kubectl",  "create",
        "namespace", universePrefix);
    return execCommand(config, commandList);
  }

  public ShellProcessHandler.ShellResponse copyEncryptionKeyFile(Map<String, String> config, String file, String universePrefix, String podName) {
    String nodePath = String.format(
            "%s/%s:/mnt/disk0",
            universePrefix,
            podName
    );
    List<String> commandList = ImmutableList.of("kubectl", "cp", file, nodePath);
    return execCommand(config, commandList);
  }

  public ShellProcessHandler.ShellResponse applySecret(Map<String, String> config, String universePrefix, String pullSecret) {
    List<String> commandList = ImmutableList.of("kubectl",  "create",
        "-f", pullSecret, "--namespace", universePrefix);
    return execCommand(config, commandList);
  }

  public ShellProcessHandler.ShellResponse helmInit(Map<String, String> config, UUID providerUUID) {
    Provider provider = Provider.get(providerUUID);
    Map<String, String> configProvider = provider.getConfig();
    if (!configProvider.containsKey("KUBECONFIG_SERVICE_ACCOUNT")) {
      throw new RuntimeException("Service Account is required.");
    }
    List<String> commandList = ImmutableList.of("helm",  "init",
        "--service-account",  configProvider.get("KUBECONFIG_SERVICE_ACCOUNT"), "--upgrade", "--wait");
    if (configProvider.containsKey("KUBECONFIG_NAMESPACE")) {
      if (configProvider.get("KUBECONFIG_NAMESPACE") != null) {
        String namespace = configProvider.get("KUBECONFIG_NAMESPACE");
        commandList = ImmutableList.of("helm",  "init",
            "--service-account",  configProvider.get("KUBECONFIG_SERVICE_ACCOUNT"), "--tiller-namespace", namespace,
            "--upgrade", "--wait");
      }
    }
    return execCommand(config, commandList);
  }

  public String getTimeout() {
    Long timeout = appConfig.getLong("yb.helm.timeout_secs");
    if (timeout == null || timeout == 0) {
      timeout = DEFAULT_TIMEOUT_SECS;
    }
    return String.valueOf(timeout);
  }

  public ShellProcessHandler.ShellResponse helmInstall(Map<String, String> config, UUID providerUUID, String universePrefix, String overridesFile) {
    String helmPackagePath = appConfig.getString("yb.helm.package");
    if (helmPackagePath == null || helmPackagePath.isEmpty()) {
      throw new RuntimeException("Helm Package path not provided.");
    }
    Provider provider = Provider.get(providerUUID);
    Map<String, String> configProvider = provider.getConfig();
    List<String> commandList = ImmutableList.of("helm",  "install",
        helmPackagePath, "--namespace", universePrefix, "--name", universePrefix, "-f", overridesFile,
        "--timeout", getTimeout(), "--wait");
    if (configProvider.containsKey("KUBECONFIG_NAMESPACE")) {
      if (configProvider.get("KUBECONFIG_NAMESPACE") != null) {
        String namespace = configProvider.get("KUBECONFIG_NAMESPACE");
        commandList = ImmutableList.of("helm",  "install",
            helmPackagePath, "--namespace", universePrefix, "--name", universePrefix, "-f", overridesFile,
            "--tiller-namespace", namespace, "--timeout", getTimeout(), "--wait");
      }
    }
    LOG.info(String.join(" ", commandList));
    return execCommand(config, commandList);
  }

  public ShellProcessHandler.ShellResponse getPodInfos(Map<String, String> config, String universePrefix) {
    List<String> commandList = ImmutableList.of("kubectl",  "get", "pods", "--namespace", universePrefix,
        "-o", "json", "-l", "release=" + universePrefix);
    return execCommand(config, commandList);
  }

  public ShellProcessHandler.ShellResponse getPodStatus(Map<String, String> config, String universePrefix, String podName) {
    List<String> commandList = ImmutableList.of("kubectl",  "get", "pod", "--namespace", universePrefix,
        "-o", "json", podName);
    return execCommand(config, commandList);
  }

  public ShellProcessHandler.ShellResponse getServiceIPs(Map<String, String> config, String universePrefix, boolean isMaster) {
    String serviceName = isMaster ? "yb-master-service" : "yb-tserver-service";
    List<String> commandList = ImmutableList.of("kubectl",  "get", "svc", serviceName, "--namespace", universePrefix,
        "-o", "jsonpath=" + SERVICE_INFO_JSONPATH);
    return execCommand(config, commandList);
  }

  public ShellProcessHandler.ShellResponse helmUpgrade(Map<String, String> config, String universePrefix, String overridesFile) {
    String helmPackagePath = appConfig.getString("yb.helm.package");
    if (helmPackagePath == null || helmPackagePath.isEmpty()) {
      throw new RuntimeException("Helm Package path not provided.");
    }
    List<String> commandList = ImmutableList.of("helm",  "upgrade",  "-f", overridesFile, "--namespace", universePrefix,
        universePrefix, helmPackagePath, "--timeout", getTimeout(), "--wait");
    LOG.info(String.join(" ", commandList));
    return execCommand(config, commandList);
  }

  public ShellProcessHandler.ShellResponse updateNumNodes(Map<String, String> config, String universePrefix, int numNodes) {
    List<String> commandList = ImmutableList.of("kubectl",  "--namespace", universePrefix, "scale", "statefulset",
        "yb-tserver", "--replicas=" + numNodes);
    return execCommand(config, commandList);
  }

  public ShellProcessHandler.ShellResponse helmDelete(Map<String, String> config, String universePrefix) {
    List<String> commandList = ImmutableList.of("helm",  "delete", universePrefix, "--purge");
    return execCommand(config, commandList);
  }

  public void deleteStorage(Map<String, String> config, String universePrefix) {
    // Delete Master Volumes
    List<String> masterCommandList = ImmutableList.of("kubectl",  "delete", "pvc",
        "--namespace", universePrefix, "-l", "app=yb-master");
    execCommand(config, masterCommandList);
    // Delete TServer Volumes
    List<String> tserverCommandList = ImmutableList.of("kubectl",  "delete", "pvc",
        "--namespace", universePrefix, "-l", "app=yb-tserver");
    execCommand(config, tserverCommandList);
    // TODO: check the execCommand outputs.
  }

  public void deleteNamespace(Map<String, String> config, String universePrefix) {
    // Delete Namespace
    List<String> masterCommandList = ImmutableList.of("kubectl",  "delete", "namespace",
        universePrefix);
    execCommand(config, masterCommandList);
  }

  private ShellProcessHandler.ShellResponse execCommand(Map<String, String> config, List<String> command) {
    return shellProcessHandler.run(command, config);
  }
}
