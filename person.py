"""Reconnaît UNE personne précise à l'image (ex. le créateur d'une campagne qui exige
d'être visible) avec les modèles OpenCV YuNet (détection) + SFace (reconnaissance).

- Les photos de référence sont dans config.yaml > person.refs (visage bien visible).
- `locate(video, t)` renvoie le centre x de la personne à l'instant t, ou None.
- `visibility(video, a, b)` = part des images de [a, b] où la personne est reconnue.
Modèles (opencv_zoo, Apache 2.0) dans models/.
"""
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).parent
DET = ROOT / "models" / "face_detection_yunet_2023mar.onnx"
REC = ROOT / "models" / "face_recognition_sface_2021dec.onnx"
MATCH = 0.36   # seuil cosinus SFace recommandé (0.363)


class Person:
    def __init__(self, refs, threshold=MATCH):
        self.det = cv2.FaceDetectorYN.create(str(DET), "", (320, 320), 0.7, 0.3, 5000)
        self.rec = cv2.FaceRecognizerSF.create(str(REC), "")
        self.threshold = threshold
        self.refs = []
        for r in refs:
            img = cv2.imread(str(ROOT / r))
            if img is None:
                raise SystemExit(f"Photo de référence introuvable : {r}")
            faces = self._faces(img)
            if not faces:
                raise SystemExit(f"Aucun visage dans la photo de référence : {r}")
            biggest = max(faces, key=lambda f: f[2] * f[3])
            self.refs.append(self._embed(img, biggest))
        self._cap, self._cap_path = None, None

    def _faces(self, img):
        h, w = img.shape[:2]
        self.det.setInputSize((w, h))
        _, faces = self.det.detect(img)
        return [] if faces is None else list(faces)

    def _embed(self, img, face):
        return self.rec.feature(self.rec.alignCrop(img, face))

    def find(self, img):
        """(centre x, largeur du visage) de la personne dans l'image, ou None."""
        scale = 640 / img.shape[1] if img.shape[1] > 640 else 1.0
        small = cv2.resize(img, None, fx=scale, fy=scale) if scale != 1.0 else img
        best, best_score = None, self.threshold
        for f in self._faces(small):
            if f[2] < 24:   # visage minuscule : pas fiable
                continue
            emb = self._embed(small, f)
            score = max(self.rec.match(emb, r, cv2.FaceRecognizerSF_FR_COSINE) for r in self.refs)
            if score >= best_score:
                best, best_score = f, score
        if best is None:
            return None
        return (best[0] + best[2] / 2) / scale, best[2] / scale

    def _frame(self, video, t):
        if self._cap_path != str(video):
            if self._cap is not None:
                self._cap.release()
            self._cap, self._cap_path = cv2.VideoCapture(str(video)), str(video)
        self._cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = self._cap.read()
        return frame if ok else None

    def release(self):
        """Ferme la vidéo ouverte (sinon Windows refuse de la supprimer)."""
        if self._cap is not None:
            self._cap.release()
        self._cap, self._cap_path = None, None

    def locate(self, video, t):
        frame = self._frame(video, t)
        if frame is None:
            return None
        hit = self.find(frame)
        return hit[0] if hit else None

    def any_face(self, video, t):
        """Centre x du plus grand visage (n'importe qui) à l'instant t, ou None."""
        frame = self._frame(video, t)
        if frame is None:
            return None
        scale = 640 / frame.shape[1] if frame.shape[1] > 640 else 1.0
        small = cv2.resize(frame, None, fx=scale, fy=scale) if scale != 1.0 else frame
        faces = [f for f in self._faces(small) if f[2] >= 24]
        if not faces:
            return None
        f = max(faces, key=lambda f: f[2] * f[3])
        return (f[0] + f[2] / 2) / scale

    def track(self, video, a, b, step=0.5):
        """Positions x de la personne de a à b (None quand elle n'est pas reconnue)."""
        out, t = [], a
        while t < b:
            out.append(self.locate(video, t))
            t += step
        return out

    def visibility(self, video, a, b, step=1.0):
        xs = self.track(video, a, b, step)
        return (sum(x is not None for x in xs) / len(xs)) if xs else 0.0


@lru_cache(maxsize=4)
def load(refs: tuple, threshold=MATCH):
    return Person(list(refs), threshold)


def from_cfg(cfg):
    p = cfg.get("person") or {}
    if not p.get("refs"):
        return None
    return load(tuple(p["refs"]), p.get("threshold", MATCH))
