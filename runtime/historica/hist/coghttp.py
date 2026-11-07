"""Minimal lazy COG reader over HTTP Range requests (GDAL's TLS is broken in this sandbox).

Supports classic TIFF/BigTIFF, tiled or stripped, deflate/none compression,
horizontal-differencing predictor, 8/16-bit samples, and overview IFDs.
"""
import struct, zlib, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
import numpy as np

TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 16: 8, 17: 8, 18: 8}
UA = "Mozilla/5.0"


class RemoteFile:
    def __init__(self, url, block=1 << 20, timeout=120):
        self.url = url
        self.block = block
        self.timeout = timeout
        self.cache = {}
        self.nreq = 0
        self.nbytes = 0
        req = urllib.request.Request(url, headers={"Range": "bytes=0-0", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            cr = r.headers.get("Content-Range", "")
        if "/" not in cr:
            raise RuntimeError("no content-range: " + str(cr))
        self.size = int(cr.rsplit("/", 1)[1])

    def _get(self, b0, b1):
        for attempt in range(7):
            try:
                req = urllib.request.Request(
                    self.url, headers={"Range": f"bytes={b0}-{b1}", "User-Agent": UA})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(min(60, 3 + 6 * attempt * attempt))
                    continue
                raise
            except Exception:
                time.sleep(min(60, 3 + 6 * attempt * attempt))
        raise RuntimeError(f"failed to fetch {b0}-{b1}")

    def read(self, offset, n):
        out = bytearray()
        while n > 0:
            b0 = (offset // self.block) * self.block
            if b0 not in self.cache:
                end = min(b0 + self.block, self.size) - 1
                data = self._get(b0, end)
                self.nreq += 1
                self.nbytes += len(data)
                self.cache[b0] = data
                if len(self.cache) > 96:
                    self.cache.pop(next(iter(self.cache)))
            blk = self.cache[b0]
            take = min(n, b0 + len(blk) - offset, b0 + self.block - offset)
            out += blk[offset - b0: offset - b0 + take]
            offset += take
            n -= take
        return bytes(out)


class Cogtif:
    def __init__(self, url):
        self.rf = RemoteFile(url)
        self.ifds = self._parse_all()
        self.native = self.ifds[0]

    # ---------------- TIFF structure ----------------
    def _parse_all(self):
        hdr = self.rf.read(0, 16)
        self.endian = "<" if hdr[:2] == b"II" else ">"
        magic = struct.unpack(self.endian + "H", hdr[2:4])[0]
        if magic == 42:
            self.big = False
            nxt = struct.unpack(self.endian + "I", hdr[4:8])[0]
        elif magic == 43:
            self.big = True
            nxt = struct.unpack(self.endian + "Q", hdr[8:16])[0]
        else:
            raise RuntimeError("not a TIFF")
        ifds, seen = [], set()
        while nxt and nxt not in seen:
            seen.add(nxt)
            ifd, nxt = self._parse_ifd(nxt)
            ifds.append(ifd)
            for s in np.atleast_1d(ifd.get(330, [])):
                if int(s) not in seen:
                    seen.add(int(s))
                    sub, _ = self._parse_ifd(int(s))
                    ifds.append(sub)
        return ifds

    def _parse_ifd(self, off):
        e, big = self.endian, self.big
        if big:
            n = struct.unpack(e + "Q", self.rf.read(off, 8))[0]
            pos, esz, inline, voffsz = off + 8, 20, 8, 8
        else:
            n = struct.unpack(e + "H", self.rf.read(off, 2))[0]
            pos, esz, inline, voffsz = off + 2, 12, 4, 4
        raw = self.rf.read(pos, n * esz)
        tags = {}
        for i in range(n):
            b = raw[i * esz:(i + 1) * esz]
            tag, typ = struct.unpack(e + "HH", b[:4])
            cnt = struct.unpack(e + ("Q" if big else "I"), b[4:4 + voffsz])[0]
            vsz = cnt * TYPE_SIZE.get(typ, 1)
            if vsz <= inline:
                voff = pos + i * esz + 4 + voffsz
            else:
                voff = struct.unpack(e + ("Q" if big else "I"),
                                     b[4 + voffsz: 8 + voffsz])[0]
            if typ == 2:
                tags[tag] = self.rf.read(voff, int(cnt))
            elif typ in (5, 10):
                tags[tag] = np.frombuffer(self.rf.read(voff, int(cnt) * 8), dtype=e + "u4").reshape(-1, 2)
            elif typ == 11:
                tags[tag] = np.frombuffer(self.rf.read(voff, int(cnt) * 4), dtype=e + "f4")
            elif typ == 12:
                tags[tag] = np.frombuffer(self.rf.read(voff, int(cnt) * 8), dtype=e + "f8")
            else:
                dt = e + ("u1" if typ in (1, 7) else "u8" if typ == 16 else "u4" if typ == 4 else "u2")
                tags[tag] = np.frombuffer(self.rf.read(voff, int(cnt) * TYPE_SIZE.get(typ, 1)),
                                          dtype=dt).astype(np.int64)
        nxt = struct.unpack(e + ("Q" if big else "I"),
                            self.rf.read(pos + n * esz, 8 if big else 4))[0]
        return tags, nxt

    # ---------------- geometry ----------------
    def _base_grid(self):
        """Level-0 (a, c, e, f): x = c + a*i, y = f + e*j."""
        if getattr(self, "_bg", None) is not None:
            return self._bg
        ifd = self.ifds[0]
        if 34264 in ifd:
            m = ifd[34264].astype("f8")
            a, c, e, f = m[0], m[3], m[4], m[7]
        else:
            sx, sy = float(ifd[33550][0]), float(ifd[33550][1])
            i0, j0, x0, y0 = (float(ifd[33922][0]), float(ifd[33922][1]),
                              float(ifd[33922][3]), float(ifd[33922][4]))
            a, e = sx, -sy
            c, f = x0 - i0 * sx, y0 + j0 * sy
        self._bg = (a, c, e, f)
        return self._bg

    def grid(self, idx=0):
        """Return (a, c, e, f) for overview level idx (overviews share top-left origin)."""
        a, c, e, f = self._base_grid()
        if idx == 0:
            return a, c, e, f
        fac = int(self.ifds[0][256][0]) / float(int(self.ifds[idx][256][0]))
        return a * fac, c, e * fac, f

    def level_for_res(self, target_res):
        """Pick the smallest level whose pixel size is still <= target_res."""
        best = 0
        for k in range(len(self.ifds)):
            a, _, _, _ = self.grid(k)
            if a <= target_res:
                best = k
            else:
                break
        return best

    def info(self):
        n = self.native
        out = []
        for k, name in [(256, "Width"), (257, "Height"), (259, "Compression"), (277, "SamplesPerPixel"),
                        (317, "Predictor"), (322, "TileWidth"), (323, "TileLength"), (278, "RowsPerStrip")]:
            if k in n:
                out.append(f"{name}={np.atleast_1d(n[k])[:2]}")
        out.append("levels=" + str([(int(i[256][0]), int(i[257][0])) for i in self.ifds]))
        return " | ".join(out)

    # ---------------- pixel access ----------------
    def read_level(self, idx, x0, y0, w, h, jobs=8):
        ifd = self.ifds[idx]
        W, H = int(ifd[256][0]), int(ifd[257][0])
        tiled = 322 in ifd
        tw, th = (int(ifd[322][0]), int(ifd[323][0])) if tiled else (W, int(ifd[278][0]))
        comp = int(np.atleast_1d(ifd.get(259, [1]))[0])
        pred = int(np.atleast_1d(ifd.get(317, [1]))[0])
        nb = int(np.atleast_1d(ifd.get(258, [16]))[0])
        offs = np.atleast_1d(ifd[324] if tiled else ifd[273]).astype(np.int64)
        cnts = np.atleast_1d(ifd[325] if tiled else ifd[279]).astype(np.int64)
        across = max(1, (W + tw - 1) // tw)
        ci, cj = x0 // tw, y0 // th
        ci_end, cj_end = (x0 + w - 1) // tw, (y0 + h - 1) // th
        npdt = np.dtype("<u2" if nb == 16 else "u1")
        outdt = np.dtype("uint16" if nb == 16 else "uint8")

        def fetch(ij):
            i, j = ij
            k = j * across + i
            if k >= len(offs) or cnts[k] == 0 or offs[k] == 0:
                return ij, None
            buf = self.rf.read(int(offs[k]), int(cnts[k]))
            arr = np.frombuffer(zlib.decompress(buf) if comp in (8, 32946) else buf, dtype=npdt)
            rows, cols = min(th, H - j * th), min(tw, W - i * tw)
            arr = arr[: rows * cols].reshape(rows, cols)
            if pred == 2:
                arr = np.cumsum(arr, axis=1, dtype=npdt)
            return ij, arr

        keys = [(i, j) for j in range(cj, cj_end + 1) for i in range(ci, ci_end + 1)]
        out = np.zeros((h, w), outdt)
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            for (i, j), arr in ex.map(fetch, keys):
                if arr is None:
                    continue
                gy0, gx0 = j * th, i * tw
                ax0, ax1 = max(x0, gx0), min(x0 + w, gx0 + arr.shape[1])
                ay0, ay1 = max(y0, gy0), min(y0 + h, gy0 + arr.shape[0])
                if ax0 < ax1 and ay0 < ay1:
                    out[ay0 - y0: ay1 - y0, ax0 - x0: ax1 - x0] = arr[ay0 - gy0: ay1 - gy0,
                                                                       ax0 - gx0: ax1 - gx0]
        return out

    def read_geo(self, bounds, target_res=None, idx=None):
        """Read a fixed-size window covering bounds=(x0,y0,x1,y1); out-of-raster pixels are 0."""
        if idx is None:
            idx = self.level_for_res(target_res) if target_res else 0
        a, c, e, f = self.grid(idx)
        x0, y0, x1, y1 = min(bounds[0], bounds[2]), min(bounds[1], bounds[3]), \
                         max(bounds[0], bounds[2]), max(bounds[1], bounds[3])
        W = max(1, int(round((x1 - x0) / a)))
        H = max(1, int(round((y1 - y0) / abs(e))))
        i0 = int(round((x0 - c) / a))
        j0 = int(round((y1 - f) / e))
        rw, rh = int(self.ifds[idx][256][0]), int(self.ifds[idx][257][0])
        outdt = np.dtype("uint16" if int(np.atleast_1d(self.ifds[idx].get(258, [16]))[0]) == 16 else "uint8")
        out = np.zeros((H, W), outdt)
        si0, sj0 = max(i0, 0), max(j0, 0)
        si1, sj1 = min(i0 + W, rw), min(j0 + H, rh)
        if si1 > si0 and sj1 > sj0:
            out[sj0 - j0: sj1 - j0, si0 - i0: si1 - i0] = \
                self.read_level(idx, si0, sj0, si1 - si0, sj1 - sj0)
        return out

    def read_geo_gt(self, bounds, target_res=None, idx=None):
        """Same as read_geo but also returns the grid: (array, x_origin, y_origin, pixel_size)."""
        if idx is None:
            idx = self.level_for_res(target_res) if target_res else 0
        a, c, e, f = self.grid(idx)
        x0 = min(bounds[0], bounds[2])
        y1 = max(bounds[1], bounds[3])
        i0 = int(round((x0 - c) / a))
        j0 = int(round((y1 - f) / e))
        return self.read_geo(bounds, idx=idx), c + i0 * a, f + j0 * e, a
