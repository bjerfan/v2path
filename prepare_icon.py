"""Place the supplied logo on an icon badge and export standard Windows sizes."""
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QColor
from PIL import Image


def main():
    app = QGuiApplication.instance() or QGuiApplication([])
    assets = Path(__file__).parent / "assets"
    source = QImage(str(assets / "logo-original.png"))
    if source.isNull():
        raise RuntimeError("Provided logo could not be loaded")
    canvas = QImage(512, 512, QImage.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#B7C5FF"))
    painter.drawRoundedRect(QRectF(8, 8, 496, 496), 112, 112)
    painter.drawImage(QRectF(66, 66, 380, 380), source)
    painter.end()
    canvas.save(str(assets / "secondnet.png"))
    # Format conversion only; all badge rendering uses the original logo above.
    with Image.open(assets / "secondnet.png") as icon:
        icon.save(assets / "secondnet.ico", sizes=[(16,16),(20,20),(24,24),(32,32),(40,40),(48,48),(64,64),(128,128),(256,256)])


if __name__ == "__main__":
    main()