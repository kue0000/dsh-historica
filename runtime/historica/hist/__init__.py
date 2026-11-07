"""historica 核心库：导入时自动修补 PROJ/GDAL 数据路径（Windows 下常见坑）。"""
import os

__version__ = "0.1.0"


def _patch_proj():
    if "PROJ_DATA" not in os.environ:
        try:
            from pyproj.datadir import get_data_dir
            d = get_data_dir()
            if d and os.path.exists(os.path.join(d, "proj.db")):
                os.environ["PROJ_DATA"] = d
                os.environ.setdefault("PROJ_LIB", d)
        except Exception:
            pass
    if "GDAL_DATA" not in os.environ:
        try:
            import rasterio
            d = os.path.join(os.path.dirname(rasterio.__file__), "gdal_data")
            if os.path.exists(d):
                os.environ["GDAL_DATA"] = d
        except Exception:
            pass


_patch_proj()
