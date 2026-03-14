from ...processors import Processor
from arch_auditor.reporter import ReportMessage, ReportType
from typing import Any

try:
    import docker

    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False


class DockerConfigSource(Processor):

    @staticmethod
    def name() -> str:
        return "DockerConfigSource"

    @staticmethod
    def requires() -> list[str]:
        return []

    def init(self, config_dict: dict) -> bool:
        self.docker_config = config_dict or {}

        if not DOCKER_AVAILABLE:
            return False

        try:
            self.client = docker.from_env()
            return True
        except Exception as e:
            self.context.reporter.report(
                ReportMessage(
                    self.name(), ReportType.ERROR, f"Failed to init Docker client: {e}"
                )
            )
            return False

    def process(self) -> None:
        docker_configs = []
        try:
            containers = self.client.containers.list()
            for container in containers:
                container_info = {
                    "id": container.id,
                    "name": container.name,
                    "image": container.image.tags,
                    "status": container.status,
                    "labels": container.labels,
                    "attrs": container.attrs,
                }
                docker_configs.append(container_info)
        except Exception as e:
            self.context.reporter.report(
                ReportMessage(
                    self.name(),
                    ReportType.ERROR,
                    f"Failed to list Docker containers: {e}",
                )
            )

        self.context.system_state.extra_attrs["docker_configs"] = docker_configs

    @staticmethod
    def has_visualization() -> bool:
        return False

    def visualize(self):
        pass
