"""
Mesh cleanup and light smoothing before GLB export.

The goal is to remove obvious extraction artifacts without changing the shape
enough to hide model-generation issues that still need to be diagnosed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import trimesh
from trimesh import repair, smoothing


@dataclass
class MeshPostprocessResult:
    mesh: trimesh.Trimesh
    diagnostics: dict[str, Any]


class MeshPostprocessService:
    def __init__(
        self,
        *,
        min_component_faces: int = 64,
        min_component_ratio: float = 0.0005,
        smoothing_iterations: int = 2,
        smoothing_lambda: float = 0.25,
    ):
        self.min_component_faces = min_component_faces
        self.min_component_ratio = min_component_ratio
        self.smoothing_iterations = smoothing_iterations
        self.smoothing_lambda = smoothing_lambda

    def process(
        self,
        mesh: trimesh.Trimesh,
        *,
        smoothing_iterations: int | None = None,
    ) -> MeshPostprocessResult:
        processed = mesh.copy()
        before = self._mesh_metrics(processed)

        self._remove_invalid_faces(processed)
        processed.remove_unreferenced_vertices()
        processed.merge_vertices()

        component_stats = self._remove_small_components(processed)
        processed.remove_unreferenced_vertices()

        try:
            repair.fix_normals(processed, multibody=True)
        except Exception:
            pass

        iterations = self.smoothing_iterations if smoothing_iterations is None else smoothing_iterations
        smoothing_applied = self._smooth(processed, iterations=max(0, int(iterations)))

        processed.remove_unreferenced_vertices()
        try:
            processed.fix_normals()
        except Exception:
            pass

        after = self._mesh_metrics(processed)
        return MeshPostprocessResult(
            mesh=processed,
            diagnostics={
                "before": before,
                "after": after,
                "components": component_stats,
                "smoothing": {
                    "applied": smoothing_applied,
                    "iterations": max(0, int(iterations)),
                    "lambda": self.smoothing_lambda,
                },
            },
        )

    def _remove_invalid_faces(self, mesh: trimesh.Trimesh):
        if len(mesh.faces) == 0:
            return

        if hasattr(mesh, "nondegenerate_faces"):
            mesh.update_faces(mesh.nondegenerate_faces())
        else:
            mesh.remove_degenerate_faces()

        if hasattr(mesh, "unique_faces"):
            mesh.update_faces(mesh.unique_faces())
        else:
            mesh.remove_duplicate_faces()

    def _remove_small_components(self, mesh: trimesh.Trimesh) -> dict[str, Any]:
        total_faces = len(mesh.faces)
        if total_faces == 0:
            return {
                "before": 0,
                "after": 0,
                "removed": 0,
                "min_faces": self.min_component_faces,
            }

        groups = trimesh.graph.connected_components(
            mesh.face_adjacency,
            nodes=np.arange(total_faces),
        )
        groups = sorted(groups, key=len, reverse=True)
        if len(groups) <= 1:
            return {
                "before": len(groups),
                "after": len(groups),
                "removed": 0,
                "min_faces": self.min_component_faces,
            }

        min_faces = max(
            self.min_component_faces,
            int(round(total_faces * self.min_component_ratio)),
        )
        kept = [group for group in groups if len(group) >= min_faces]
        if not kept:
            kept = [groups[0]]

        removed = len(groups) - len(kept)
        if removed > 0:
            mesh.update_faces(np.concatenate(kept))

        return {
            "before": len(groups),
            "after": len(kept),
            "removed": removed,
            "min_faces": min_faces,
            "largest_faces": int(len(groups[0])),
        }

    def _smooth(self, mesh: trimesh.Trimesh, *, iterations: int) -> bool:
        if iterations <= 0 or len(mesh.vertices) == 0 or len(mesh.faces) == 0:
            return False

        try:
            smoothing.filter_laplacian(
                mesh,
                lamb=self.smoothing_lambda,
                iterations=iterations,
                volume_constraint=True,
            )
            return True
        except Exception:
            return False

    def _mesh_metrics(self, mesh: trimesh.Trimesh) -> dict[str, Any]:
        bounds = getattr(mesh, "bounds", None)
        extents = getattr(mesh, "extents", None)
        return {
            "vertices": int(len(getattr(mesh, "vertices", []))),
            "faces": int(len(getattr(mesh, "faces", []))),
            "bounds": bounds.tolist() if bounds is not None else None,
            "extents": extents.tolist() if extents is not None else None,
            "is_watertight": bool(getattr(mesh, "is_watertight", False)),
        }


mesh_postprocess_service = MeshPostprocessService()
