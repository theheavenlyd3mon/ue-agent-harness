"""Project-aware tools."""

import json
from pathlib import Path
from typing import Any

from dataclasses import dataclass


@dataclass
class ProjectTools:
    config: Any

    def scan_project(self, uproject_path: str) -> dict:
        path = Path(uproject_path)
        if not path.exists():
            return {"error": f"uproject not found: {uproject_path}"}

        with open(path, "r") as f:
            raw = json.load(f)

        source_root = path.parent / "Source"
        modules = []
        if source_root.exists():
            for item in source_root.iterdir():
                if item.is_dir() and (item / f"{item.name}.Build.cs").exists():
                    modules.append({
                        "name": item.name,
                        "path": str(item),
                        "build_cs": str(item / f"{item.name}.Build.cs"),
                    })

        return {
            "name": path.stem,
            "path": str(path),
            "engine_version": raw.get("EngineAssociation", "unknown"),
            "modules": modules,
        }

    def read_build_cs(self, module_name: str) -> dict:
        path = Path(self.config.uproject_path).parent / "Source" / module_name / f"{module_name}.Build.cs"
        if not path.exists():
            return {"error": f"Build.cs not found: {path}"}
        return {"path": str(path), "content": path.read_text()}
