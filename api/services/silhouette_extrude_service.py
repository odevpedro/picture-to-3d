"""
Silhouette extrusion for flat, thin objects.

This creates a shape-preserving slab from the sanitized foreground mask. It is
intended for icons, weapons, logos and other mostly-front-facing objects where
single-image volumetric reconstruction tends to inflate the silhouette.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import trimesh
from PIL import Image
from skimage import measure


@dataclass
class SilhouetteExtrudeResult:
    mesh: trimesh.Trimesh
    diagnostics: dict[str, Any]


class SilhouetteExtrudeService:
    def __init__(
        self,
        *,
        mask_size: int = 192,
        depth_voxels: int = 12,
        depth_scale: float = 0.08,
        alpha_threshold: int = 8,
    ):
        self.mask_size = mask_size
        self.depth_voxels = depth_voxels
        self.depth_scale = depth_scale
        self.alpha_threshold = alpha_threshold

    def process(
        self,
        image: Image.Image,
        alpha: Image.Image,
        *,
        depth_scale: float | None = None,
    ) -> SilhouetteExtrudeResult:
        depth_scale = self.depth_scale if depth_scale is None else float(depth_scale)
        mask = self._prepare_mask(alpha)
        volume = np.repeat(mask[None, :, :], self.depth_voxels, axis=0)
        volume = np.pad(volume, ((1, 1), (1, 1), (1, 1)), constant_values=False)
        volume = volume.astype(np.float32)

        verts, faces, _, _ = measure.marching_cubes(volume, level=0.5)
        vertices = self._normalize_vertices(verts, mask.shape, depth_scale)
        vertex_colors = self._sample_vertex_colors(image, verts, mask.shape)

        mesh = trimesh.Trimesh(
            vertices=vertices,
            faces=faces.astype(np.int64),
            vertex_colors=vertex_colors,
            process=False,
        )
        mesh.remove_unreferenced_vertices()
        try:
            mesh.fix_normals()
        except Exception:
            pass

        diagnostics = {
            "mode": "silhouette",
            "mask_size": list(mask.shape[::-1]),
            "depth_voxels": self.depth_voxels,
            "depth_scale": depth_scale,
            "mask_area_ratio": round(float(mask.mean()), 4),
            "vertices": int(len(mesh.vertices)),
            "faces": int(len(mesh.faces)),
            "extents": mesh.extents.tolist() if mesh.extents is not None else None,
        }
        return SilhouetteExtrudeResult(mesh=mesh, diagnostics=diagnostics)

    def _prepare_mask(self, alpha: Image.Image) -> np.ndarray:
        resized = alpha.convert("L").resize(
            (self.mask_size, self.mask_size),
            Image.Resampling.LANCZOS,
        )
        mask = np.asarray(resized, dtype=np.uint8) > self.alpha_threshold

        try:
            from scipy import ndimage
            mask = ndimage.binary_fill_holes(mask)
            mask = ndimage.binary_closing(mask, structure=np.ones((3, 3), dtype=bool))
        except Exception:
            pass

        if not mask.any():
            raise ValueError("Silhouette mask is empty")
        return mask.astype(bool)

    def _normalize_vertices(
        self,
        verts: np.ndarray,
        mask_shape: tuple[int, int],
        depth_scale: float,
    ) -> np.ndarray:
        height, width = mask_shape
        unpadded = verts - 1.0
        z = (unpadded[:, 0] / max(1, self.depth_voxels - 1) - 0.5) * depth_scale
        y = 0.5 - unpadded[:, 1] / max(1, height - 1)
        x = unpadded[:, 2] / max(1, width - 1) - 0.5
        return np.column_stack([x * 2.0, y * 2.0, z]).astype(np.float32)

    def _sample_vertex_colors(
        self,
        image: Image.Image,
        verts: np.ndarray,
        mask_shape: tuple[int, int],
    ) -> np.ndarray:
        rgb = image.convert("RGB").resize(
            mask_shape[::-1],
            Image.Resampling.LANCZOS,
        )
        pixels = np.asarray(rgb, dtype=np.uint8)
        unpadded = verts - 1.0
        ys = np.clip(np.rint(unpadded[:, 1]).astype(int), 0, mask_shape[0] - 1)
        xs = np.clip(np.rint(unpadded[:, 2]).astype(int), 0, mask_shape[1] - 1)
        colors = pixels[ys, xs]
        alpha = np.full((colors.shape[0], 1), 255, dtype=np.uint8)
        return np.concatenate([colors, alpha], axis=1)


silhouette_extrude_service = SilhouetteExtrudeService()
