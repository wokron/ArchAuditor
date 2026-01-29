from pathlib import Path

from fastapi.responses import HTMLResponse

from ...processors import Processor


class ResourceAuditVisualization(Processor):
    @staticmethod
    def name() -> str:
        return "ResourceAuditVisualization"

    @staticmethod
    def requires() -> list[str]:
        return []

    def init(self, config: dict) -> bool:
        return True

    def process(self) -> None:
        pass

    @staticmethod
    def has_visualization() -> bool:
        return True

    def visualize(self):
        template_path = (
            Path(__file__).resolve().parents[2] / "templates" / "Resource_audit.html"
        )
        html = template_path.read_text(encoding="utf-8")
        return HTMLResponse(content=html)
