from PIL import Image, ImageDraw

from api.services.silhouette_extrude_service import SilhouetteExtrudeService


def test_silhouette_extrude_creates_thin_textured_mesh():
    image = Image.new("RGB", (128, 128), "white")
    alpha = Image.new("L", (128, 128), 0)

    image_draw = ImageDraw.Draw(image)
    alpha_draw = ImageDraw.Draw(alpha)
    shape = [(24, 28), (104, 64), (24, 100)]
    image_draw.polygon(shape, fill=(40, 40, 40))
    alpha_draw.polygon(shape, fill=255)

    service = SilhouetteExtrudeService(mask_size=64, depth_voxels=8, depth_scale=0.06)
    result = service.process(image, alpha)

    assert len(result.mesh.vertices) > 0
    assert len(result.mesh.faces) > 0
    assert result.diagnostics["mode"] == "silhouette"
    assert result.mesh.extents[2] < result.mesh.extents[0]
    assert result.mesh.extents[2] < result.mesh.extents[1]
