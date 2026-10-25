import sys, json, os
from dataclasses import dataclass
from PySide6.QtCore import Qt, QPoint, QRect, QRectF, QEvent, QSize, QSettings, QStandardPaths, QTimer, QThread, Signal
from PySide6.QtGui import QAction, QColor, QImage, QPainter, QPen, QPixmap, QGuiApplication, QPolygon, QBrush, QLinearGradient, QPainterPath, QIcon
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QDockWidget, QListWidget,
    QListWidgetItem, QToolBar, QColorDialog, QFileDialog, QMessageBox, QComboBox, QLabel,
    QSpinBox, QSlider, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout, QInputDialog,
    QLineEdit, QFormLayout, QDialog, QCheckBox, QToolButton, QMenu, QSplitter, QScrollArea, QSizePolicy, QTabWidget, QSpinBox, QDoubleSpinBox, QDialogButtonBox, QStyle, QRubberBand)

APP_NAME = "Neural Sprite"
DEFAULT_SIZE = 32
# Traditional artist's color wheel: primary, secondary, and tertiary colors.
PALETTE = ["#ff0000", "#ffff00", "#0000ff", "#ff8000", "#008000", "#800080",
           "#ff4000", "#ffbf00", "#80ff00", "#008080", "#4000ff", "#c00080"]

@dataclass
class ArtLayer:
    name: str
    image: QImage
    visible: bool = True
    opacity: int = 255

class PixelCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 420)
        self.canvas_w = DEFAULT_SIZE
        self.canvas_h = DEFAULT_SIZE
        self.zoom = 12
        self.show_grid = True
        self.tool = "Pencil"
        self.color = QColor("#a83e2b")
        self.brush_size = 1
        self.fill_shapes = True
        self.symmetry = "Off"
        self.layers = [ArtLayer("Layer 1", QImage(self.canvas_w, self.canvas_h, QImage.Format_ARGB32))]
        self.layers[0].image.fill(Qt.transparent)
        self.active_layer = 0
        self.undo_stack = []
        self.redo_stack = []
        self.last_point = None
        self.drag_start = None
        self.preview = None
        self.pan_offset = QPoint(0, 0)
        self._panning = False
        self._pan_last = None
        self._hover_pixel = None
        self.selection_shape = "Rectangle"
        self.selection_rect = None
        self.selection_preview = None
        self.selection_mask = None  # set[(x, y)] for irregular selections such as Magic Wand
        self.move_image = None
        self.move_origin = None
        self.move_offset = QPoint(0, 0)
        self.on_layers_changed = None
        self.on_status = None
        self.on_zoom_changed = None
        self.on_color_picked = None
        self._merged_cache = None
        self._checker_cache = None
        self._preview_cache = None
        self._preview_cache_key = None
        self._wheel_delta_remainder = 0
        self._selection_outline_cache = None
        self._selection_outline_key = None
        self.setMouseTracking(True)

    def snapshot(self):
        self.undo_stack.append(([ArtLayer(x.name, x.image.copy(), x.visible, x.opacity) for x in self.layers], self.active_layer))
        if len(self.undo_stack) > 80: self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self):
        if not self.undo_stack: return
        self.redo_stack.append(([ArtLayer(x.name, x.image.copy(), x.visible, x.opacity) for x in self.layers], self.active_layer))
        self.layers, self.active_layer = self.undo_stack.pop()
        self._changed(True)

    def redo(self):
        if not self.redo_stack: return
        self.undo_stack.append(([ArtLayer(x.name, x.image.copy(), x.visible, x.opacity) for x in self.layers], self.active_layer))
        self.layers, self.active_layer = self.redo_stack.pop()
        self._changed(True)

    def _changed(self, refresh_layers=False):
        self._merged_cache = None
        self._preview_cache = None
        self._preview_cache_key = None
        self._selection_outline_cache = None
        self._selection_outline_key = None
        self.update()
        if refresh_layers and self.on_layers_changed: self.on_layers_changed()

    def set_zoom(self, value):
        self.zoom = float(value)
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        return super().sizeHint()

    def image_rect(self):
        w, h = round(self.canvas_w*self.zoom), round(self.canvas_h*self.zoom)
        return QRect((self.width()-w)//2 + self.pan_offset.x(), (self.height()-h)//2 + self.pan_offset.y(), w, h)

    def _compose_merged_image(self):
        result = QImage(self.canvas_w, self.canvas_h, QImage.Format_ARGB32)
        result.fill(Qt.transparent)
        painter = QPainter(result)
        for layer in self.layers:
            if layer.visible:
                painter.setOpacity(layer.opacity/255.0)
                painter.drawImage(0, 0, layer.image)
        painter.end()
        self._merged_cache = result
        return result

    def merged_image(self):
        # Cached flattened image is treated as read-only by callers.
        return self._merged_cache if self._merged_cache is not None else self._compose_merged_image()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#202328"))
        rect = self.image_rect()
        # Clip all expensive canvas work to the part Qt actually asked us to repaint.
        # This is critical for large zoomed canvases: mouse movement should not walk
        # every checker tile and every grid line outside the visible widget.
        dirty = event.rect().intersected(rect)
        # Draw a cached checker texture rather than issuing tens of thousands of
        # fillRect calls on every repaint (the main source of the previous lag).
        if not dirty.isEmpty():
            if self._checker_cache is None or self._checker_cache.size() != QSize(self.canvas_w, self.canvas_h):
                checker = QImage(self.canvas_w, self.canvas_h, QImage.Format_RGB32)
                checker.fill(QColor("#e8e8e8"))
                cp = QPainter(checker)
                cp.fillRect(QRect(0, 0, self.canvas_w, self.canvas_h), QColor("#e8e8e8"))
                # Pixel-art transparency squares are 8x8 canvas pixels, scaled with the canvas.
                tile_px = 8
                for yy in range(0, self.canvas_h, tile_px):
                    for xx in range(0, self.canvas_w, tile_px):
                        if ((xx // tile_px) + (yy // tile_px)) % 2:
                            cp.fillRect(QRect(xx, yy, min(tile_px,self.canvas_w-xx), min(tile_px,self.canvas_h-yy)), QColor("#c8c8c8"))
                cp.end(); self._checker_cache = checker
            p.drawImage(rect, self._checker_cache)
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        if self._merged_cache is None:
            self._compose_merged_image()
        p.drawImage(rect, self._merged_cache)
        if self.show_grid and self.zoom >= 5 and not dirty.isEmpty():
            pen = QPen(QColor(50, 50, 50, 65), 1)
            p.setPen(pen)
            x_start = max(0, int((dirty.left() - rect.left()) // self.zoom))
            x_end = min(self.canvas_w, int((dirty.right() - rect.left()) // self.zoom) + 1)
            y_start = max(0, int((dirty.top() - rect.top()) // self.zoom))
            y_end = min(self.canvas_h, int((dirty.bottom() - rect.top()) // self.zoom) + 1)
            for x in range(x_start, x_end+1):
                px = rect.left()+round(x*self.zoom)
                p.drawLine(px, max(rect.top(), dirty.top()), px, min(rect.bottom(), dirty.bottom()))
            for y in range(y_start, y_end+1):
                py = rect.top()+round(y*self.zoom)
                p.drawLine(max(rect.left(), dirty.left()), py, min(rect.right(), dirty.right()), py)
        p.setPen(QPen(QColor("#aab0ba"), 1))
        p.drawRect(rect)
        # Top and left rulers
        p.fillRect(QRect(rect.left(), max(0, rect.top()-19), rect.width(), 18), QColor("#30343b"))
        p.fillRect(QRect(max(0, rect.left()-32), rect.top(), 31, rect.height()), QColor("#30343b"))
        p.setPen(QColor("#c5cad2"))
        step = 8 if self.canvas_w > 64 else 4
        for i in range(0, self.canvas_w+1, step):
            x = rect.left()+round(i*self.zoom)
            p.drawLine(x, max(0, rect.top()-7), x, rect.top())
            p.drawText(x+2, max(10, rect.top()-7), str(i))
        for i in range(0, self.canvas_h+1, step):
            y = rect.top()+round(i*self.zoom)
            p.drawLine(max(0, rect.left()-7), y, rect.left(), y)
            p.drawText(max(0, rect.left()-29), y-2, str(i))
        if self.preview is not None and self.drag_start is not None:
            key = (self.canvas_w, self.canvas_h, self.drag_start.x(), self.drag_start.y(),
                   self.preview.x(), self.preview.y(), self.tool, self.color.name(), self.symmetry, self.fill_shapes, int(QGuiApplication.keyboardModifiers()))
            if self._preview_cache is None or self._preview_cache_key != key:
                preview_img = QImage(self.canvas_w, self.canvas_h, QImage.Format_ARGB32)
                preview_img.fill(Qt.transparent)
                pp = QPainter(preview_img); pp.setRenderHint(QPainter.Antialiasing, False)
                outline = QColor(self.color); outline.setAlpha(255)
                pp.setPen(QPen(outline, 1, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
                a, b = self.drag_start, self.preview
                rpx = self._selection_rect_from_modifiers(a, b, QGuiApplication.keyboardModifiers())
                if self.fill_shapes:
                    pp.setBrush(QBrush(outline))
                else:
                    pp.setBrush(Qt.NoBrush)
                if self.tool == "Line": pp.drawLine(a,b)
                elif self.tool == "Rectangle": pp.drawRect(rpx)
                elif self.tool == "Ellipse": pp.drawEllipse(rpx)
                elif self.tool == "Triangle": pp.drawPolygon(self._triangle_polygon(a,b, QGuiApplication.keyboardModifiers()))
                pp.end()
                self._preview_cache = preview_img; self._preview_cache_key = key
            p.drawImage(rect, self._preview_cache)
            if self.symmetry == "Vertical": p.drawImage(rect, self._preview_cache.mirrored(True, False))
            elif self.symmetry == "Horizontal": p.drawImage(rect, self._preview_cache.mirrored(False, True))
        if self.tool == "Move" and self.move_image is not None and self.move_origin is not None:
            moved = self.move_origin + self.move_offset
            p.drawImage(QRect(rect.left()+round(moved.x()*self.zoom), rect.top()+round(moved.y()*self.zoom),
                              round(self.move_image.width()*self.zoom), round(self.move_image.height()*self.zoom)), self.move_image)
        # Always show the symmetry origin whenever symmetry is enabled, not only while dragging.
        if self.symmetry == "Vertical":
            axis_x = rect.left() + round(self.canvas_w * self.zoom / 2)
            p.setPen(QPen(QColor("#54d6ff"), 1, Qt.DashLine))
            p.drawLine(axis_x, rect.top(), axis_x, rect.bottom())
        elif self.symmetry == "Horizontal":
            axis_y = rect.top() + round(self.canvas_h * self.zoom / 2)
            p.setPen(QPen(QColor("#54d6ff"), 1, Qt.DashLine))
            p.drawLine(rect.left(), axis_y, rect.right(), axis_y)
        # Draw selection contours on pixel-grid boundaries. Rectangular/elliptical
        # selections use geometric membership; Magic Wand uses an exact pixel mask.
        sel = self.selection_preview if self.selection_preview is not None else self.selection_rect
        if self.tool == "Move" and self.move_image is not None and self.move_origin is not None and self.selection_rect is not None:
            sel = QRect(self.selection_rect)
            sel.translate(self.move_offset)
        has_selection = (self.selection_mask is not None or sel is not None)
        # Render the live selection directly in widget coordinates. This avoids a
        # pixel-by-pixel outline rebuild on every move and makes the preview obvious.
        if self.selection_preview is not None and self.tool in ("Select Rectangle", "Select Ellipse"):
            sr = self.selection_preview
            preview_box = QRectF(rect.left() + sr.x() * self.zoom,
                                 rect.top() + sr.y() * self.zoom,
                                 sr.width() * self.zoom, sr.height() * self.zoom)
            p.save()
            p.setClipRect(rect)
            p.setBrush(QBrush(QColor(70, 190, 255, 32)))
            p.setPen(QPen(QColor("#ffffff"), 1, Qt.DashLine))
            if self.selection_shape == "Ellipse": p.drawEllipse(preview_box)
            else: p.drawRect(preview_box)
            p.setPen(QPen(QColor("#111111"), 1, Qt.DotLine))
            inner_box = preview_box.adjusted(1, 1, -1, -1)
            if self.selection_shape == "Ellipse": p.drawEllipse(inner_box)
            else: p.drawRect(inner_box)
            p.restore()
        if has_selection and self.selection_preview is None and (self.tool in ("Select Rectangle", "Select Ellipse", "Magic Wand") or self.selection_rect is not None):
            if self.selection_mask is not None and self.selection_preview is None and self.tool != "Move":
                bounds = self.selection_rect
                mask_key = ("mask", bounds.x(), bounds.y(), bounds.width(), bounds.height(), frozenset(self.selection_mask))
                if self._selection_outline_cache is None or self._selection_outline_key != mask_key:
                    edges = []
                    for xx, yy in self.selection_mask:
                        lx, ly = xx - bounds.x(), yy - bounds.y()
                        color = "#ffffff" if (xx + yy) % 2 == 0 else "#111111"
                        if (xx, yy-1) not in self.selection_mask: edges.append((lx, ly, lx+1, ly, color))
                        if (xx-1, yy) not in self.selection_mask: edges.append((lx, ly, lx, ly+1, color))
                        if (xx+1, yy) not in self.selection_mask: edges.append((lx+1, ly, lx+1, ly+1, color))
                        if (xx, yy+1) not in self.selection_mask: edges.append((lx, ly+1, lx+1, ly+1, color))
                    self._selection_outline_cache = edges
                    self._selection_outline_key = mask_key
            else:
                key = (sel.x(), sel.y(), sel.width(), sel.height(), self.selection_shape, self.tool)
                if self._selection_outline_cache is None or self._selection_outline_key != key:
                    edges = []
                    def inside_local(x, y):
                        if x < 0 or y < 0 or x >= sel.width() or y >= sel.height(): return False
                        return self._preview_point_selected(QPoint(sel.x()+x, sel.y()+y), sel)
                    for yy in range(sel.height()):
                        for xx in range(sel.width()):
                            if not inside_local(xx, yy): continue
                            color = "#ffffff" if (xx + yy) % 2 == 0 else "#111111"
                            if not inside_local(xx, yy-1): edges.append((xx, yy, xx+1, yy, color))
                            if not inside_local(xx-1, yy): edges.append((xx, yy, xx, yy+1, color))
                            if not inside_local(xx+1, yy): edges.append((xx+1, yy, xx+1, yy+1, color))
                            if not inside_local(xx, yy+1): edges.append((xx, yy+1, xx+1, yy+1, color))
                    self._selection_outline_cache = edges
                    self._selection_outline_key = key
            for x1, y1, x2, y2, color in self._selection_outline_cache or []:
                p.setPen(QPen(QColor(color), 1, Qt.SolidLine, Qt.SquareCap))
                p.drawLine(rect.left()+round((sel.x()+x1)*self.zoom),
                           rect.top()+round((sel.y()+y1)*self.zoom),
                           rect.left()+round((sel.x()+x2)*self.zoom),
                           rect.top()+round((sel.y()+y2)*self.zoom))
        # Brush footprint preview follows the cursor for pencil and eraser.
        if self._hover_pixel is not None and self.tool in ("Pencil", "Eraser") and not self._panning:
            hp = self._hover_pixel
            br = QRect(rect.left()+round(hp.x()*self.zoom), rect.top()+round(hp.y()*self.zoom),
                       max(1,round(self.brush_size*self.zoom)), max(1,round(self.brush_size*self.zoom)))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor("#ffffff"), 1, Qt.DashLine))
            p.drawRect(br.adjusted(1, 1, -1, -1))
            p.setPen(QPen(QColor("#111111"), 1, Qt.SolidLine))
            p.drawRect(br.adjusted(0, 0, -1, -1))

    def point_to_pixel(self, pos):
        """Return a canvas pixel only when the pointer is over the canvas."""
        point = self._raw_point_to_pixel(pos)
        if 0 <= point.x() < self.canvas_w and 0 <= point.y() < self.canvas_h:
            return point
        return None

    def _raw_point_to_pixel(self, pos):
        """Map widget coordinates to pixel coordinates, including outside the canvas."""
        r = self.image_rect()
        x = int((pos.x() - r.left()) // self.zoom)
        y = int((pos.y() - r.top()) // self.zoom)
        return QPoint(x, y)

    def _clipped_selection_rect(self, a, b, modifiers):
        """Build a selection rectangle clipped to the canvas pixel bounds."""
        requested = self._selection_rect_from_modifiers(a, b, modifiers)
        return requested.intersected(QRect(0, 0, self.canvas_w, self.canvas_h))

    def _selection_rect(self, a, b):
        return QRect(min(a.x(),b.x()), min(a.y(),b.y()), abs(a.x()-b.x())+1, abs(a.y()-b.y())+1)

    def _selection_rect_from_modifiers(self, a, b, modifiers):
        """Build selection bounds honoring Ctrl (square/circle) and Alt (centered)."""
        dx, dy = b.x() - a.x(), b.y() - a.y()
        if modifiers & Qt.ControlModifier:
            side = max(abs(dx), abs(dy))
            dx = side if dx >= 0 else -side
            dy = side if dy >= 0 else -side
        if modifiers & Qt.AltModifier:
            half_x, half_y = abs(dx), abs(dy)
            return QRect(a.x()-half_x, a.y()-half_y, 2*half_x+1, 2*half_y+1)
        return QRect(min(a.x(), a.x()+dx), min(a.y(), a.y()+dy), abs(dx)+1, abs(dy)+1)

    def _preview_point_selected(self, pt, r):
        if not r.contains(pt): return False
        if self.selection_shape != "Ellipse": return True
        rx = max(0.5, r.width()/2.0); ry = max(0.5, r.height()/2.0)
        cx = r.x() + (r.width()-1)/2.0; cy = r.y() + (r.height()-1)/2.0
        return ((pt.x()-cx)/rx)**2 + ((pt.y()-cy)/ry)**2 <= 1.0

    def _point_selected(self, pt):
        if self.selection_mask is not None:
            return (pt.x(), pt.y()) in self.selection_mask
        if self.selection_rect is None:
            return True
        return self._preview_point_selected(pt, self.selection_rect)

    def _selection_clip_path(self):
        path = QPainterPath()
        if self.selection_mask is not None:
            for x, y in self.selection_mask:
                path.addRect(x, y, 1, 1)
            return path
        if self.selection_rect is None:
            return path
        r = self.selection_rect
        if self.selection_shape == "Ellipse":
            path.addEllipse(r.x(), r.y(), r.width(), r.height())
        else:
            path.addRect(r.x(), r.y(), r.width(), r.height())
        return path

    def _magic_wand_select(self, pt):
        """Select the 4-connected region matching the clicked pixel's exact RGBA value."""
        image = self.merged_image()
        target = image.pixelColor(pt)
        width, height = self.canvas_w, self.canvas_h
        target_rgba = target.rgba()
        mask = set()
        stack = [(pt.x(), pt.y())]
        # Mark pixels when enqueued to avoid duplicate work on large regions.
        seen = {(pt.x(), pt.y())}
        while stack:
            x, y = stack.pop()
            if image.pixel(x, y) != target_rgba:
                continue
            mask.add((x, y))
            for nx, ny in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)):
                if 0 <= nx < width and 0 <= ny < height and (nx,ny) not in seen:
                    seen.add((nx,ny))
                    stack.append((nx,ny))
        self.selection_mask = mask
        if mask:
            xs = [xy[0] for xy in mask]; ys = [xy[1] for xy in mask]
            self.selection_rect = QRect(min(xs), min(ys), max(xs)-min(xs)+1, max(ys)-min(ys)+1)
        else:
            self.selection_rect = None
        self.selection_shape = "Magic Wand"
        self.selection_preview = None
        self._selection_outline_cache = None
        self._selection_outline_key = None
        self.update()

    def deselect(self):
        self.selection_rect = None
        self.selection_preview = None
        self.selection_mask = None
        self._selection_outline_cache = None
        self._selection_outline_key = None
        self.move_image = None
        self.move_origin = None
        self.update()

    def _triangle_polygon(self, a, b, modifiers=Qt.NoModifier):
        # Ctrl constrains the bounding box; Alt centers the shape on the first point.
        r = self._selection_rect_from_modifiers(a, b, modifiers)
        left, right = r.left(), r.right()
        top, bottom = r.top(), r.bottom()
        if modifiers & Qt.AltModifier:
            # Use a centered isosceles triangle so the first click is its center.
            return QPolygon([QPoint((left+right)//2, top), QPoint(right, bottom), QPoint(left, bottom)])
        # Preserve the directional triangle behavior for an unconstrained drag.
        dx, dy = b.x()-a.x(), b.y()-a.y()
        if modifiers & Qt.ControlModifier:
            side = max(abs(dx), abs(dy))
            dx = side if dx >= 0 else -side
            dy = side if dy >= 0 else -side
            b = QPoint(a.x()+dx, a.y()+dy)
            r = self._selection_rect(a, b)
            left, right, top, bottom = r.left(), r.right(), r.top(), r.bottom()
        if abs(dx) >= abs(dy):
            half = max(1, (bottom-top)//2)
            base = QPoint(b.x(), b.y())
            return QPolygon([QPoint(a.x(),a.y()), QPoint(base.x(),base.y()-half), QPoint(base.x(),base.y()+half)])
        half = max(1, (right-left)//2)
        base = QPoint(b.x(),b.y())
        return QPolygon([QPoint(a.x(),a.y()), QPoint(base.x()-half,base.y()), QPoint(base.x()+half,base.y())])

    def _draw_pixel(self, pt):
        if pt is None: return
        layer = self.layers[self.active_layer]
        points = [pt]
        if self.symmetry == "Vertical": points.append(QPoint(self.canvas_w - 1 - pt.x(), pt.y()))
        elif self.symmetry == "Horizontal": points.append(QPoint(pt.x(), self.canvas_h - 1 - pt.y()))
        seen = set()
        for point in points:
            if not self._point_selected(point):
                continue
            key = (point.x(), point.y())
            if key in seen: continue
            seen.add(key)
            p = QPainter(layer.image)
            if self.selection_rect is not None:
                p.setClipPath(self._selection_clip_path())
            if self.tool == "Eraser":
                p.setCompositionMode(QPainter.CompositionMode_Clear)
                p.fillRect(QRect(point.x(), point.y(), self.brush_size, self.brush_size), Qt.transparent)
            else:
                p.setPen(Qt.NoPen); p.setBrush(self.color)
                p.drawRect(QRect(point.x(), point.y(), self.brush_size, self.brush_size))
            p.end()
        self._merged_cache = None

    def _symmetry_lines(self, a, b):
        lines = [(a, b)]
        if self.symmetry == "Vertical":
            lines.append((QPoint(self.canvas_w - 1 - a.x(), a.y()), QPoint(self.canvas_w - 1 - b.x(), b.y())))
        elif self.symmetry == "Horizontal":
            lines.append((QPoint(a.x(), self.canvas_h - 1 - a.y()), QPoint(b.x(), self.canvas_h - 1 - b.y())))
        # Avoid drawing twice on the axis itself.
        unique = []
        seen = set()
        for x, y in lines:
            key = (x.x(), x.y(), y.x(), y.y())
            if key not in seen:
                seen.add(key); unique.append((x, y))
        return unique

    def _draw_line(self, a, b, color=None):
        if a is None or b is None: return
        img = self.layers[self.active_layer].image
        for start, end in self._symmetry_lines(a, b):
            p = QPainter(img)
            if self.selection_rect is not None:
                p.setClipPath(self._selection_clip_path())
            if self.tool == "Eraser":
                p.setCompositionMode(QPainter.CompositionMode_Clear)
                p.setPen(QPen(QColor(0,0,0,0), self.brush_size, Qt.SolidLine, Qt.SquareCap))
            else:
                p.setPen(QPen(color or self.color, self.brush_size, Qt.SolidLine, Qt.SquareCap))
            p.drawLine(start, end)
            p.end()
        self._merged_cache = None

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if event.modifiers() & Qt.AltModifier:
            self.pan_offset.setX(self.pan_offset.x() + round(delta / 120 * 40))
        elif event.modifiers() & Qt.ControlModifier:
            self.pan_offset.setY(self.pan_offset.y() + round(delta / 120 * 40))
        elif delta:
            cursor = event.position().toPoint()
            old_rect = self.image_rect()
            old_zoom = self.zoom
            canvas_x = (cursor.x() - old_rect.left()) / old_zoom
            canvas_y = (cursor.y() - old_rect.top()) / old_zoom
            # Use proportional zoom increments (about 8% per wheel notch), rather
            # than integer pixel-size steps that are huge at low magnifications.
            self._wheel_delta_remainder += delta
            notches = self._wheel_delta_remainder / 120.0
            if abs(notches) >= 0.01:
                self._wheel_delta_remainder -= int(notches * 120)
                new_zoom = max(0.15, min(48.0, old_zoom * (1.08 ** notches)))
                self.zoom = new_zoom
                base_left = (self.width() - round(self.canvas_w * self.zoom)) // 2
                base_top = (self.height() - round(self.canvas_h * self.zoom)) // 2
                self.pan_offset.setX(round(cursor.x() - canvas_x * self.zoom - base_left))
                self.pan_offset.setY(round(cursor.y() - canvas_y * self.zoom - base_top))
                self.updateGeometry()
                if self.on_zoom_changed: self.on_zoom_changed(self.zoom)
                if self.on_status: self.on_status(f"Zoom: {round(self.zoom / 12 * 100)}%  |  Wheel: zoom to cursor, Alt+wheel: horizontal pan, Ctrl+wheel: vertical pan")
        self.update()
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._panning = True
            self._pan_last = event.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept(); return
        pt = self.point_to_pixel(event.position().toPoint())
        if event.button() != Qt.LeftButton: return
        if pt is None:
            if self.tool in ("Select Rectangle", "Select Ellipse", "Magic Wand"):
                self.deselect()
            return
        if self.tool == "Picker":
            c = self.layers[self.active_layer].image.pixelColor(pt)
            if c.alpha() == 0: c = self.merged_image().pixelColor(pt)
            if c.alpha():
                self.color = c
                if self.on_color_picked: self.on_color_picked(c)
            self.update(); return
        if self.tool == "Magic Wand":
            self._magic_wand_select(pt)
            return
        if self.tool in ("Select Rectangle", "Select Ellipse"):
            self.selection_rect = None; self.selection_mask = None
            self._selection_outline_cache = None; self._selection_outline_key = None
            self.drag_start = pt; self.preview = pt; self.selection_shape = "Ellipse" if self.tool == "Select Ellipse" else "Rectangle"
            self.selection_preview = self._selection_rect_from_modifiers(pt, pt, event.modifiers()); self.update(); return
        if self.tool == "Move":
            if self.selection_rect is None or not self._point_selected(pt):
                return
            self.snapshot()
            r = self.selection_rect
            layer_img = self.layers[self.active_layer].image
            self.move_image = QImage(r.size(), QImage.Format_ARGB32); self.move_image.fill(Qt.transparent)
            mp = QPainter(self.move_image)
            local_clip = QPainterPath()
            if self.selection_shape == "Ellipse":
                local_clip.addEllipse(0, 0, r.width(), r.height())
            else:
                local_clip.addRect(0, 0, r.width(), r.height())
            mp.setClipPath(local_clip)
            mp.drawImage(-r.x(), -r.y(), layer_img)
            mp.end()
            # Clear only pixels inside the actual selected region.
            clear = QPainter(layer_img); clear.setCompositionMode(QPainter.CompositionMode_Clear)
            clear.setClipPath(self._selection_clip_path()); clear.fillRect(r, Qt.transparent); clear.end()
            self.move_origin = QPoint(r.x(), r.y()); self.move_offset = QPoint(0,0)
            self.drag_start = pt; self.last_point = pt; self._changed(); return
        self.snapshot()
        self.last_point = pt
        self.drag_start = pt
        if self.tool in ("Pencil", "Eraser"):
            self._draw_pixel(pt); self._changed()
        elif self.tool == "Fill":
            self._flood_fill(pt); self._changed()
        elif self.tool in ("Line", "Rectangle", "Ellipse", "Triangle"):
            self.preview = pt
        self.update()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self._panning and self._pan_last is not None:
            delta = pos - self._pan_last
            self.pan_offset += delta
            self._pan_last = pos
            self.update(); return
        pt = self.point_to_pixel(pos)
        old_hover = self._hover_pixel
        self._hover_pixel = pt
        if old_hover != pt:
            dirty = QRect()
            r = self.image_rect()
            for hp in (old_hover, pt):
                if hp is not None:
                    br = QRect(r.left()+round(hp.x()*self.zoom), r.top()+round(hp.y()*self.zoom),
                               max(1,round(self.brush_size*self.zoom)), max(1,round(self.brush_size*self.zoom))).adjusted(-2,-2,2,2)
                    dirty = dirty.united(br)
            if not dirty.isEmpty(): self.update(dirty)
        if self.on_status and pt: self.on_status(f"X: {pt.x()}   Y: {pt.y()}   Canvas: {self.canvas_w}×{self.canvas_h}   Zoom: {round(self.zoom / 12 * 100)}%")
        if event.buttons() & Qt.LeftButton and self.drag_start is not None and self.tool in ("Select Rectangle", "Select Ellipse"):
            raw_pt = self._raw_point_to_pixel(pos)
            self.selection_preview = self._clipped_selection_rect(self.drag_start, raw_pt, event.modifiers())
            self._selection_outline_cache = None
            self._selection_outline_key = None
            self.update()
        elif event.buttons() & Qt.LeftButton and pt:
            if self.tool == "Move" and self.move_image is not None and self.last_point is not None:
                self.move_offset = pt - self.drag_start
                self.update()
            elif self.tool in ("Pencil", "Eraser"):
                previous = self.last_point
                self._draw_line(previous, pt)
                self.last_point = pt
                self._merged_cache = None
                if previous is not None:
                    r = self.image_rect()
                    x1, y1 = previous.x(), previous.y(); x2, y2 = pt.x(), pt.y()
                    dirty = QRect(r.left()+round(min(x1,x2)*self.zoom), r.top()+round(min(y1,y2)*self.zoom),
                                  round((abs(x2-x1)+1)*self.zoom), round((abs(y2-y1)+1)*self.zoom)).adjusted(-round(self.zoom), -round(self.zoom), round(self.zoom), round(self.zoom))
                    if self.symmetry != "Off": self.update()
                    else: self.update(dirty)
            elif self.tool in ("Line", "Rectangle", "Ellipse", "Triangle"):
                self.preview = pt; self._preview_cache = None; self._preview_cache_key = None; self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton and self._panning:
            self._panning = False; self._pan_last = None
            self.unsetCursor(); event.accept(); return
        pos = event.position().toPoint()
        pt = self.point_to_pixel(pos)
        if event.button() == Qt.LeftButton and self.drag_start is not None and self.tool in ("Select Rectangle", "Select Ellipse"):
            raw_pt = self._raw_point_to_pixel(pos)
            self.selection_rect = self._clipped_selection_rect(self.drag_start, raw_pt, event.modifiers())
            self.selection_preview = None
            self.selection_mask = None
            self._selection_outline_cache = None; self._selection_outline_key = None
            self.selection_shape = "Ellipse" if self.tool == "Select Ellipse" else "Rectangle"
        elif event.button() == Qt.LeftButton and self.drag_start is not None and pt is not None:
            if self.tool == "Move" and self.move_image is not None:
                target = self.move_origin + self.move_offset
                painter = QPainter(self.layers[self.active_layer].image)
                painter.drawImage(target, self.move_image); painter.end()
                self.selection_rect.translate(self.move_offset)
                if self.selection_mask is not None:
                    dx, dy = self.move_offset.x(), self.move_offset.y()
                    self.selection_mask = {(x+dx, y+dy) for x, y in self.selection_mask
                                           if 0 <= x+dx < self.canvas_w and 0 <= y+dy < self.canvas_h}
                self._selection_outline_cache = None; self._selection_outline_key = None
                self.move_image = None; self.move_origin = None; self.move_offset = QPoint(0,0)
            elif self.tool == "Line":
                self._draw_line(self.drag_start, pt)
            elif self.tool in ("Rectangle", "Ellipse", "Triangle"):
                r = self._selection_rect_from_modifiers(self.drag_start, pt, event.modifiers())
                p = QPainter(self.layers[self.active_layer].image)
                if self.selection_rect is not None:
                    p.setClipPath(self._selection_clip_path())
                p.setPen(QPen(self.color, 1))
                p.setBrush(self.color if self.fill_shapes else Qt.NoBrush)
                def draw_shape():
                    if self.tool == "Rectangle": p.drawRect(r)
                    elif self.tool == "Ellipse": p.drawEllipse(r)
                    else: p.drawPolygon(self._triangle_polygon(self.drag_start, pt, event.modifiers()))
                draw_shape()
                if self.symmetry == "Vertical":
                    p.save(); p.translate(self.canvas_w-1, 0); p.scale(-1, 1); draw_shape(); p.restore()
                elif self.symmetry == "Horizontal":
                    p.save(); p.translate(0, self.canvas_h-1); p.scale(1, -1); draw_shape(); p.restore()
                p.end()
        self.last_point = None; self.drag_start = None; self.preview = None; self.selection_preview = None; self._preview_cache = None; self._preview_cache_key = None
        self._changed()

    def _flood_fill(self, pt):
        img = self.layers[self.active_layer].image
        if not self._point_selected(pt): return
        target = img.pixelColor(pt)
        replacement = self.color
        if target == replacement: return
        stack = [(pt.x(), pt.y())]
        seen = set()
        while stack and len(seen) < self.canvas_w*self.canvas_h:
            x,y = stack.pop()
            if (x,y) in seen or not (0 <= x < self.canvas_w and 0 <= y < self.canvas_h): continue
            if not self._point_selected(QPoint(x,y)): continue
            seen.add((x,y))
            if img.pixelColor(x,y) != target: continue
            img.setPixelColor(x,y,replacement)
            stack.extend([(x+1,y),(x-1,y),(x,y+1),(x,y-1)])

    def add_layer(self, name=None):
        self.snapshot()
        self.layers.append(ArtLayer(name or f"Layer {len(self.layers)+1}", QImage(self.canvas_w,self.canvas_h,QImage.Format_ARGB32)))
        self.layers[-1].image.fill(Qt.transparent)
        self.active_layer = len(self.layers)-1
        self._changed(True)

    def delete_layer(self):
        if len(self.layers) <= 1: return
        self.snapshot(); self.layers.pop(self.active_layer); self.active_layer = max(0,self.active_layer-1); self._changed(True)

    def resize_canvas(self, w, h):
        self.snapshot()
        for layer in self.layers:
            new = QImage(w,h,QImage.Format_ARGB32); new.fill(Qt.transparent)
            p = QPainter(new); p.drawImage(0,0,layer.image); p.end()
            layer.image = new
        self.canvas_w,self.canvas_h = w,h
        self._checker_cache = None
        self._changed(True)

class HSVColorSelector(QWidget):
    """Compact Photoshop-style hue strip plus saturation/value square."""
    def __init__(self, initial_color, on_change, parent=None):
        super().__init__(parent)
        self.setMinimumSize(205, 190)
        self.hue = max(0, initial_color.hsvHue()) if initial_color.hsvHue() >= 0 else 0
        self.saturation = initial_color.hsvSaturation()
        self.value = initial_color.value()
        self.on_change = on_change
        self._square = QRect()
        self._hue_rect = QRect()

    def selected_color(self):
        return QColor.fromHsv(self.hue, self.saturation, self.value)

    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing, False)
        margin, gap, strip_w = 2, 10, 16
        self._hue_rect = QRect(self.width()-margin-strip_w, margin, strip_w, self.height()-2*margin)
        self._square = QRect(margin, margin, max(20, self._hue_rect.left()-gap-margin), self.height()-2*margin)
        hue_color = QColor.fromHsv(self.hue, 255, 255)
        p.fillRect(self._square, hue_color)
        white = QLinearGradient(self._square.left(), 0, self._square.right(), 0)
        white.setColorAt(0, QColor(255,255,255)); white.setColorAt(1, QColor(255,255,255,0))
        p.fillRect(self._square, white)
        black = QLinearGradient(0, self._square.top(), 0, self._square.bottom())
        black.setColorAt(0, QColor(0,0,0,0)); black.setColorAt(1, QColor(0,0,0,255))
        p.fillRect(self._square, black)
        rainbow = QLinearGradient(0, self._hue_rect.top(), 0, self._hue_rect.bottom())
        for pos, hue in [(0,0),(1/6,60),(2/6,120),(3/6,180),(4/6,240),(5/6,300),(1,359)]:
            rainbow.setColorAt(pos, QColor.fromHsv(hue,255,255))
        p.fillRect(self._hue_rect, rainbow)
        sx = self._square.left() + round(self.saturation/255 * (self._square.width()-1))
        sy = self._square.bottom() - round(self.value/255 * (self._square.height()-1))
        p.setPen(QPen(Qt.black, 3)); p.drawEllipse(QPoint(sx,sy),5,5)
        p.setPen(QPen(Qt.white,1)); p.drawEllipse(QPoint(sx,sy),5,5)
        hy = self._hue_rect.top() + round(self.hue/359 * (self._hue_rect.height()-1))
        p.setPen(QPen(Qt.black,2)); p.drawRect(self._hue_rect.left()-2,hy-2,self._hue_rect.width()+4,4)
        p.setPen(QPen(Qt.white,1)); p.drawRect(self._hue_rect.left()-1,hy-1,self._hue_rect.width()+2,2)

    def _set_from_pos(self, pos):
        if self._hue_rect.contains(pos):
            self.hue = max(0,min(359,round((pos.y()-self._hue_rect.top())/max(1,self._hue_rect.height()-1)*359)))
        elif self._square.contains(pos):
            self.saturation = max(0,min(255,round((pos.x()-self._square.left())/max(1,self._square.width()-1)*255)))
            self.value = max(0,min(255,round((self._square.bottom()-pos.y())/max(1,self._square.height()-1)*255)))
        else: return
        self.update(); self.on_change(self.selected_color())

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton: self._set_from_pos(event.position().toPoint())
    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton: self._set_from_pos(event.position().toPoint())
    def set_color(self, color):
        h,s,v=color.getHsv()
        if h >= 0: self.hue=h
        self.saturation=max(0,s); self.value=max(0,v); self.update()

class ColorPanel(QWidget):
    """Color picker and palette are separate, focused tabs; the layer dock is independent."""
    def __init__(self, canvas):
        super().__init__(); self.canvas=canvas; self.custom_colors=[]
        root=QVBoxLayout(self); root.setContentsMargins(6,6,6,6); root.setSpacing(6)
        self.tabs=QTabWidget(); root.addWidget(self.tabs)
        picker=QWidget(); pl=QVBoxLayout(picker); pl.setContentsMargins(4,4,4,4); pl.setSpacing(6)
        self.selector=HSVColorSelector(self.canvas.color,self._selector_color_changed)
        pl.addWidget(self.selector, 1)
        self.preview_swatch=QLabel(" "); self.preview_swatch.setFixedHeight(22); pl.addWidget(self.preview_swatch)
        hexrow=QHBoxLayout(); hexrow.addWidget(QLabel("Hex"))
        self.hex_edit=QLineEdit(self.canvas.color.name().upper()); self.hex_edit.setMaxLength(7); self.hex_edit.setPlaceholderText("#RRGGBB")
        self.hex_edit.editingFinished.connect(self.apply_hex); hexrow.addWidget(self.hex_edit,1); pl.addLayout(hexrow)
        self.screen_pick_button=QPushButton("Pick Screen Color…"); self.screen_pick_button.setToolTip("Click anywhere on your screen to sample that pixel color"); self.screen_pick_button.clicked.connect(self.pick_screen_color); pl.addWidget(self.screen_pick_button)
        add=QPushButton("Add Current Color to Palette"); add.clicked.connect(lambda:self.add_custom(self.canvas.color)); pl.addWidget(add)
        self.tabs.addTab(picker,"Color Picker")

        palette=QWidget(); pal=QVBoxLayout(palette); pal.setContentsMargins(5,5,5,5); pal.setSpacing(8)
        pal.addWidget(QLabel("Common Colors")); self.common_grid=QGridLayout(); self.common_grid.setSpacing(4); pal.addLayout(self.common_grid)
        common=[("Black","#000000"),("White","#ffffff"),("Red","#ff0000"),("Orange","#ff8000"),("Yellow","#ffff00"),("Green","#00a040"),("Cyan","#00c8d7"),("Blue","#164dff"),("Purple","#8000b5"),("Pink","#ff4fa3"),("Brown","#804020"),("Gray","#808080")]
        for i,(name,hx) in enumerate(common):
            b=self._swatch_button(hx,f"{name} — {hx.upper()}"); b.clicked.connect(lambda checked=False,c=hx:self.set_color(QColor(c))); self.common_grid.addWidget(b,i//4,i%4)
        pal.addWidget(QLabel("My Colors  ·  right-click a swatch to remove")); self.custom_grid=QGridLayout(); self.custom_grid.setSpacing(4); pal.addLayout(self.custom_grid); pal.addStretch()
        self.tabs.addTab(palette,"Palette")
        self.update_swatch(); self.refresh_custom()

    def _swatch_button(self,hx,tooltip=""):
        b=QPushButton(); b.setFixedSize(27,25); b.setToolTip(tooltip or hx.upper()); b.setStyleSheet(f"background:{hx}; border:1px solid #777; padding:0px;"); return b

    def _selector_color_changed(self,c):
        if c.isValid(): self.canvas.color=c; self.update_swatch(); self.canvas.update()

    def set_color(self,c,add_custom=False):
        if not c.isValid(): return
        self.canvas.color=c; self.selector.set_color(c); self.update_swatch(); self.canvas.update()
        if add_custom: self.add_custom(c)

    def add_custom(self,c):
        if not c.isValid(): return
        hx=c.name().lower()
        if hx not in self.custom_colors:
            self.custom_colors.append(hx); self.refresh_custom(); self.tabs.setCurrentIndex(1)

    def apply_hex(self):
        text=self.hex_edit.text().strip()
        if not text.startswith('#'): text='#'+text
        c=QColor(text)
        if c.isValid(): self.set_color(c)
        else: self.update_swatch()

    def pick_color(self):
        c=QColorDialog.getColor(self.canvas.color,self,"Choose Color",QColorDialog.ShowAlphaChannel)
        if c.isValid(): self.set_color(c,True)

    def pick_screen_color(self):
        self._screen_picker = ScreenColorPicker(self)
        self._screen_picker.color_picked.connect(self._finish_screen_pick)
        self._screen_picker.show_picker()

    def _finish_screen_pick(self, color):
        if color.isValid():
            self.set_color(color, True)


    def refresh_custom(self):
        while self.custom_grid.count():
            item=self.custom_grid.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        for i,hx in enumerate(self.custom_colors):
            b=self._swatch_button(hx,hx.upper()+" — click to select; right-click to remove")
            b.clicked.connect(lambda checked=False,c=hx:self.set_color(QColor(c)))
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda pos,c=hx:self.remove_custom(c))
            self.custom_grid.addWidget(b,i//5,i%5)

    def remove_custom(self,hx):
        if hx in self.custom_colors: self.custom_colors.remove(hx); self.refresh_custom()

    def update_swatch(self):
        self.hex_edit.setText(self.canvas.color.name().upper())
        self.preview_swatch.setStyleSheet(f"background:{self.canvas.color.name()}; border:1px solid #777;")

class ScreenColorPicker(QWidget):
    """Fullscreen click-to-sample overlay for picking a color from any visible screen pixel."""
    color_picked = Signal(QColor)

    def __init__(self, parent=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self._virtual_geometry = QGuiApplication.primaryScreen().virtualGeometry()
        for screen in QGuiApplication.screens():
            self._virtual_geometry = self._virtual_geometry.united(screen.geometry())
        self.setGeometry(self._virtual_geometry)
        self._hint = QLabel("Click anywhere to pick a screen color  •  Esc to cancel", self)
        self._hint.setStyleSheet("background:rgba(25,28,32,220); color:white; padding:8px 12px; border:1px solid #777; border-radius:4px;")
        self._hint.adjustSize()
        self._hint.move(16, 16)

    def show_picker(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 28))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            self.close()
            return
        global_pos = event.globalPosition().toPoint()
        self.hide()
        QApplication.processEvents()
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        origin = screen.geometry().topLeft()
        local_x, local_y = global_pos.x() - origin.x(), global_pos.y() - origin.y()
        pixmap = screen.grabWindow(0, local_x, local_y, 1, 1)
        color = pixmap.toImage().pixelColor(0, 0) if not pixmap.isNull() else QColor()
        self.color_picked.emit(color)
        self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)


class LayersPanel(QWidget):
    def __init__(self, canvas):
        super().__init__(); self.canvas=canvas; self._refreshing=False
        layout=QVBoxLayout(self); layout.setContentsMargins(6,6,6,6)
        self.list=QListWidget(); self.list.setDragDropMode(QListWidget.InternalMove); self.list.setDefaultDropAction(Qt.MoveAction)
        self.list.currentRowChanged.connect(self.select_layer); self.list.model().rowsMoved.connect(self.sync_drag_order); self.list.itemChanged.connect(self.itemChanged); layout.addWidget(self.list)
        row=QHBoxLayout()
        for label,fn in [("+",self.add),("−",self.delete),("↑",self.up),("↓",self.down)]:
            b=QPushButton(label); b.clicked.connect(fn); row.addWidget(b)
        layout.addLayout(row); self.refresh()
    def refresh(self):
        self._refreshing=True
        active_obj=self.canvas.layers[self.canvas.active_layer] if self.canvas.layers else None
        self.list.blockSignals(True); self.list.clear()
        # Canvas storage is bottom-to-top; list is top-to-bottom.
        for layer in reversed(self.canvas.layers):
            item=QListWidgetItem(layer.name); item.setData(Qt.UserRole, id(layer)); item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
            item.setCheckState(Qt.Checked if layer.visible else Qt.Unchecked)
            self.list.addItem(item)
        row=next((i for i in range(self.list.count()) if self.list.item(i).data(Qt.UserRole)==id(active_obj)),0)
        self.list.setCurrentRow(row); self.list.blockSignals(False); self._refreshing=False
    def select_layer(self,row):
        if self._refreshing or row < 0: return
        objid=self.list.item(row).data(Qt.UserRole)
        for i,layer in enumerate(self.canvas.layers):
            if id(layer)==objid: self.canvas.active_layer=i; self.canvas.update(); return
    def sync_drag_order(self,*args):
        if self._refreshing: return
        objs=[self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]
        by_id={id(layer):layer for layer in self.canvas.layers}
        if len(objs)==len(self.canvas.layers) and all(x in by_id for x in objs):
            active_obj=self.canvas.layers[self.canvas.active_layer]
            self.canvas.snapshot(); self.canvas.layers=[by_id[x] for x in reversed(objs)]; self.canvas.active_layer=self.canvas.layers.index(active_obj)
            self.canvas._changed()
    def add(self):
        name,ok=QInputDialog.getText(self,"New layer","Layer name:",text=f"Layer {len(self.canvas.layers)+1}")
        if ok: self.canvas.add_layer(name); self.refresh()
    def delete(self): self.canvas.delete_layer(); self.refresh()
    def _move(self,delta):
        i=self.canvas.active_layer; j=i+delta
        if 0 <= j < len(self.canvas.layers):
            self.canvas.snapshot(); self.canvas.layers[i],self.canvas.layers[j]=self.canvas.layers[j],self.canvas.layers[i]; self.canvas.active_layer=j; self.canvas._changed(True); self.refresh()
    def up(self): self._move(1)
    def down(self): self._move(-1)
    def itemChanged(self,item):
        if self._refreshing: return
        objid=item.data(Qt.UserRole)
        for layer in self.canvas.layers:
            if id(layer)==objid:
                layer.visible=item.checkState()==Qt.Checked; self.canvas._merged_cache=None; self.canvas.update(); break

class CanvasSizeDialog(QDialog):
    def __init__(self, width, height, parent=None):
        super().__init__(parent); self.setWindowTitle("Canvas Size"); self.setMinimumWidth(280)
        layout=QVBoxLayout(self); form=QFormLayout()
        self.width_spin=QSpinBox(); self.width_spin.setRange(1,512); self.width_spin.setValue(width)
        self.height_spin=QSpinBox(); self.height_spin.setRange(1,512); self.height_spin.setValue(height)
        form.addRow("Width (px)", self.width_spin); form.addRow("Height (px)", self.height_spin); layout.addLayout(form)
        self.lock=QCheckBox("Lock width and height together"); layout.addWidget(self.lock)
        self.lock.toggled.connect(self._lock_changed); self.width_spin.valueChanged.connect(self._width_changed); self.height_spin.valueChanged.connect(self._height_changed)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
    def _lock_changed(self, checked):
        if checked: self.height_spin.setValue(self.width_spin.value())
    def _width_changed(self, value):
        if self.lock.isChecked(): self.height_spin.blockSignals(True); self.height_spin.setValue(value); self.height_spin.blockSignals(False)
    def _height_changed(self, value):
        if self.lock.isChecked(): self.width_spin.blockSignals(True); self.width_spin.setValue(value); self.width_spin.blockSignals(False)
    def dimensions(self): return self.width_spin.value(), self.height_spin.value()

class SettingsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("AI Provider Settings")
        self.setMinimumWidth(500)
        self.settings = QSettings("NeuralSprite", "NeuralSprite")
        form = QFormLayout(self)
        self.provider = QComboBox()
        self.provider.addItems(["OpenAI Cloud", "Local ComfyUI"])
        self.provider.setCurrentText(self.settings.value("ai/provider", "OpenAI Cloud", type=str))
        self.cloud = QLineEdit(self.settings.value("ai/openai_api_key", "", type=str))
        self.cloud.setEchoMode(QLineEdit.Password)
        self.cloud.setPlaceholderText("OpenAI API key")
        self.model = QComboBox()
        self.model.addItems(["gpt-image-2.5-sunburst", "gpt-image-2.5-flare", "gpt-image-2"])
        saved_model = self.settings.value("ai/openai_model", "gpt-image-2.5-sunburst", type=str)
        self.model.setCurrentText(saved_model)
        self.local = QLineEdit(self.settings.value("ai/local_endpoint", "http://127.0.0.1:8188", type=str))
        self.local.setPlaceholderText("http://127.0.0.1:8188")
        self.checkpoint = QLineEdit(self.settings.value("ai/local_checkpoint", "", type=str))
        self.checkpoint.setPlaceholderText("Optional; blank = use ComfyUI's first checkpoint")
        form.addRow("Active provider", self.provider)
        form.addRow("OpenAI API key", self.cloud)
        form.addRow("Cloud image model", self.model)
        form.addRow("Local ComfyUI URL", self.local)
        form.addRow("Local checkpoint", self.checkpoint)
        note = QLabel("OpenAI requests use your API account and may incur charges. Local ComfyUI runs on your own machine or server; install and start ComfyUI with its API server enabled and a compatible checkpoint. Local mode does not require an OpenAI key. The API key is stored in this Windows user's application settings and is not encrypted by Neural Sprite.")
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def save(self):
        self.settings.setValue("ai/provider", self.provider.currentText())
        self.settings.setValue("ai/openai_api_key", self.cloud.text().strip())
        self.settings.setValue("ai/openai_model", self.model.currentText())
        self.settings.setValue("ai/local_endpoint", self.local.text().strip().rstrip("/"))
        self.settings.setValue("ai/local_checkpoint", self.checkpoint.text().strip())
        self.accept()


class AIImageWorker(QThread):
    succeeded = Signal(object, str, str)
    failed = Signal(str)

    def __init__(self, provider, api_key, model, endpoint, checkpoint, prompt, source_png, operation, target_size, ai_options=None, parent=None):
        super().__init__(parent)
        self.provider, self.api_key, self.model = provider, api_key, model
        self.endpoint, self.checkpoint = endpoint.rstrip("/"), checkpoint.strip()
        self.prompt, self.source_png, self.operation = prompt, source_png, operation
        self.target_size = target_size
        self.ai_options = ai_options or {}
        self.elapsed_seconds = 0.0
        self.diagnostics = ""

    def run(self):
        import time
        started = time.monotonic()
        try:
            if self.provider == "Local ComfyUI":
                image_bytes = self._run_comfyui()
            else:
                image_bytes = self._run_openai()
            image = QImage.fromData(image_bytes, "PNG")
            if image.isNull():
                raise RuntimeError("The AI provider returned an image that could not be decoded as PNG.")
            w, h = self.target_size
            # SDXL checkpoints need a model-appropriate working resolution. Tiny native
            # latent grids (e.g. 32x32) are not enough for SDXL to compose a coherent sprite.
            # Convert the finished image to the exact canvas grid with nearest-neighbour
            # sampling, which preserves hard pixel edges rather than smoothing them.
            if image.width() != w or image.height() != h:
                image = image.scaled(w, h, Qt.IgnoreAspectRatio, Qt.FastTransformation)
            image = image.convertToFormat(QImage.Format_ARGB32)
            self.elapsed_seconds = time.monotonic() - started
            self.diagnostics = self._diagnostics_text()
            self.succeeded.emit(image, self.operation, self.diagnostics)
        except Exception as exc:
            self.failed.emit(str(exc))

    def _diagnostics_text(self):
        opts = self.ai_options
        lines = [f"Provider: {self.provider}", f"Operation: {self.operation}",
                 f"Elapsed: {self.elapsed_seconds:.1f} seconds", f"Final size: {self.target_size[0]} × {self.target_size[1]} px"]
        if self.provider == "Local ComfyUI":
            lines.extend([f"Quality preset: {opts.get('quality', 'Balanced')}",
                          f"Sampling: {opts.get('steps', 30)} steps, CFG {opts.get('cfg', 6.0)}",
                          f"Sampler / scheduler: {opts.get('sampler', 'euler_ancestral')} / {opts.get('scheduler', 'normal')}",
                          f"Working resolution: {opts.get('working_edge', 1024)} px longest edge",
                          f"Second refinement pass: {'On' if opts.get('second_pass') else 'Off'}"])
        return "\n".join(lines)

    def _run_openai(self):
        import urllib.request, urllib.error, base64, uuid
        if not self.api_key:
            raise RuntimeError("No OpenAI API key is configured. Choose Local ComfyUI in AI Provider Settings or add an OpenAI API key.")
        endpoint = "https://api.openai.com/v1/images/edits" if self.source_png else "https://api.openai.com/v1/images/generations"
        prompt = self._pixel_prompt()
        boundary = "----NeuralSprite" + uuid.uuid4().hex
        parts = []
        def field(name, value):
            parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n").encode("utf-8"))
        field("model", self.model); field("prompt", prompt); field("quality", "medium"); field("output_format", "png"); field("size", "auto")
        if self.source_png:
            parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"reference.png\"\r\nContent-Type: image/png\r\n\r\n").encode("utf-8") + self.source_png + b"\r\n")
        body = b"".join(parts) + f"--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(endpoint, data=body, headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "multipart/form-data; boundary=" + boundary}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=300) as response: payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try: detail = json.loads(detail).get("error", {}).get("message", detail)
            except Exception: pass
            raise RuntimeError(f"OpenAI image API returned HTTP {exc.code}: {detail}")
        images = payload.get("data") or []
        if not images or not images[0].get("b64_json"): raise RuntimeError("The image API returned no image data.")
        return base64.b64decode(images[0]["b64_json"])

    def _pixel_prompt(self):
        width, height = self.target_size
        if self.provider == "Local ComfyUI":
            size_instruction = (
                f"\n\nFINAL SPRITE TARGET: {width} × {height} pixels. Create a simple, clearly readable 2D game sprite "
                "with a strong silhouette, centered composition, clean separation between subject and background, "
                "and generous margins. It will be converted to the target pixel grid, so avoid tiny details, "
                "fine textures, gradients, and thin lines. Keep the entire subject inside the frame."
            )
        else:
            size_instruction = ""
        extra = ""
        if self.ai_options.get("mechanical_guidance"):
            extra = ("\n\nMECHANICAL STRUCTURE GUIDANCE: Components must be physically connected and geometrically coherent. "
                     "For tracked vehicles, use one continuous closed-loop track on each visible side, with distinct evenly spaced links "
                     "following the loop around aligned drive wheels, idlers, and road wheels. No floating, broken, tangled, or duplicated parts. "
                     "Preserve plausible assembly, alignment, and consistent scale. Do not invent extra components.")
        negative = self.ai_options.get("negative_prompt", "").strip()
        if negative:
            extra += "\n\nAVOID: " + negative
        return (self.prompt.strip() + size_instruction + extra +
                "\n\nCreate a clean 2D pixel-art game sprite. Use crisp pixel clusters, a limited cohesive palette, "
                "sharp edges, no blur, no antialiasing, and no text unless specifically requested. "
                "Keep the main subject clearly readable at the requested native sprite size.")

    def _run_comfyui(self):
        import urllib.request, urllib.error, urllib.parse, time, uuid
        base = self.endpoint
        if not base: raise RuntimeError("Enter your ComfyUI server URL in AI Provider Settings.")
        def request(path, data=None, headers=None, timeout=20):
            req = urllib.request.Request(base + path, data=data, headers=headers or {}, method="POST" if data is not None else "GET")
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp: return resp.read()
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"ComfyUI returned HTTP {exc.code} for {path}: {detail[:900]}")
            except Exception as exc:
                raise RuntimeError(f"Could not reach ComfyUI at {base}. Make sure ComfyUI is running with its API server enabled. Details: {exc}")
        try: info = json.loads(request("/object_info").decode("utf-8"))
        except Exception as exc: raise RuntimeError(f"Unable to read ComfyUI node information. Check the server URL. Details: {exc}")
        ckpts = (((info.get("CheckpointLoaderSimple") or {}).get("input") or {}).get("required") or {}).get("ckpt_name", [[]])[0]
        if not ckpts: raise RuntimeError("ComfyUI did not report any checkpoints. Install a compatible checkpoint in ComfyUI/models/checkpoints and restart ComfyUI.")
        # Validate sampler and scheduler against this ComfyUI server rather than assuming
        # every install has the same choices or custom nodes.
        sampler_inputs = (((info.get("KSampler") or {}).get("input") or {}).get("required") or {})
        available_samplers = (sampler_inputs.get("sampler_name") or [[]])[0]
        available_schedulers = (sampler_inputs.get("scheduler") or [[]])[0]
        if available_samplers:
            requested_sampler = self.ai_options.get("sampler", "euler_ancestral")
            self.ai_options["sampler"] = requested_sampler if requested_sampler in available_samplers else ("euler_ancestral" if "euler_ancestral" in available_samplers else available_samplers[0])
        if available_schedulers:
            requested_scheduler = self.ai_options.get("scheduler", "normal")
            self.ai_options["scheduler"] = requested_scheduler if requested_scheduler in available_schedulers else ("normal" if "normal" in available_schedulers else available_schedulers[0])
        checkpoint = self.checkpoint if self.checkpoint else ckpts[0]
        if checkpoint not in ckpts:
            raise RuntimeError(f"Checkpoint '{checkpoint}' was not found by ComfyUI. Available checkpoints: {', '.join(ckpts[:12])}")
        width, height = self.target_size
        # SDXL needs a much larger latent grid than a typical sprite canvas. Generate at
        # 1024px on the long edge (aspect ratio preserved), then convert to target dimensions
        # in run() using nearest-neighbour sampling. Round working dimensions to multiples of 64.
        working_edge = int(self.ai_options.get("working_edge", 1024))
        scale = working_edge / max(width, height)
        gen_width = max(64, int(round((width * scale) / 64.0)) * 64)
        gen_height = max(64, int(round((height * scale) / 64.0)) * 64)
        prompt = self._pixel_prompt()
        client_id = str(uuid.uuid4())
        if self.source_png:
            # Upscale the reference with nearest-neighbour before VAE encoding so SDXL
            # receives a viable latent size while retaining the source's hard pixel edges.
            source_image = QImage.fromData(self.source_png, "PNG")
            if source_image.isNull(): raise RuntimeError("The reference image could not be decoded.")
            source_image = source_image.scaled(gen_width, gen_height, Qt.IgnoreAspectRatio, Qt.FastTransformation)
            buffer = QBuffer()
            buffer.open(QIODevice.WriteOnly)
            source_image.save(buffer, "PNG")
            upload_png = bytes(buffer.data())
            boundary = "----PixelForgeUpload" + uuid.uuid4().hex
            body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"pixelforge-reference.png\"\r\nContent-Type: image/png\r\n\r\n").encode() + upload_png + f"\r\n--{boundary}--\r\n".encode()
            uploaded = json.loads(request("/upload/image", body, {"Content-Type": "multipart/form-data; boundary="+boundary}, timeout=60).decode())
            filename = uploaded.get("name")
            if not filename: raise RuntimeError("ComfyUI accepted the reference upload but did not return a filename.")
            workflow = {
                "1":{"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":checkpoint}},
                "2":{"class_type":"LoadImage","inputs":{"image":filename}},
                "3":{"class_type":"VAEEncode","inputs":{"pixels":["2",0],"vae":["1",2]}},
                "4":{"class_type":"CLIPTextEncode","inputs":{"text":prompt,"clip":["1",1]}},
                "5":{"class_type":"CLIPTextEncode","inputs":{"text":self.ai_options.get("negative_prompt", "blurry, smooth gradients, antialiasing, text, watermark, low contrast"),"clip":["1",1]}},
                "6":{"class_type":"KSampler","inputs":{"seed":int(time.time()) % 2147483647,"steps":int(self.ai_options.get("steps", 30)),"cfg":float(self.ai_options.get("cfg", 6.0)),"sampler_name":self.ai_options.get("sampler", "euler_ancestral"),"scheduler":self.ai_options.get("scheduler", "normal"),"denoise":float(self.ai_options.get("refine_strength", 0.55)),"model":["1",0],"positive":["4",0],"negative":["5",0],"latent_image":["3",0]}},
                "7":{"class_type":"VAEDecode","inputs":{"samples":["6",0],"vae":["1",2]}},
                "8":{"class_type":"SaveImage","inputs":{"filename_prefix":"NeuralSprite","images":["7",0]}}
            }
        else:
            workflow = {
                "1":{"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":checkpoint}},
                "2":{"class_type":"CLIPTextEncode","inputs":{"text":prompt,"clip":["1",1]}},
                "3":{"class_type":"CLIPTextEncode","inputs":{"text":self.ai_options.get("negative_prompt", "blurry, smooth gradients, antialiasing, text, watermark, low contrast"),"clip":["1",1]}},
                "4":{"class_type":"EmptyLatentImage","inputs":{"width":gen_width,"height":gen_height,"batch_size":1}},
                "5":{"class_type":"KSampler","inputs":{"seed":int(time.time()) % 2147483647,"steps":int(self.ai_options.get("steps", 30)),"cfg":float(self.ai_options.get("cfg", 6.0)),"sampler_name":self.ai_options.get("sampler", "euler_ancestral"),"scheduler":self.ai_options.get("scheduler", "normal"),"denoise":1.0,"model":["1",0],"positive":["2",0],"negative":["3",0],"latent_image":["4",0]}},
                "6":{"class_type":"VAEDecode","inputs":{"samples":["5",0],"vae":["1",2]}},
                "7":{"class_type":"SaveImage","inputs":{"filename_prefix":"NeuralSprite","images":["6",0]}}
            }
        payload = json.dumps({"prompt":workflow,"client_id":client_id}).encode("utf-8")
        queued = json.loads(request("/prompt", payload, {"Content-Type":"application/json"}, timeout=30).decode("utf-8"))
        prompt_id = queued.get("prompt_id")
        if not prompt_id:
            errors = queued.get("node_errors")
            raise RuntimeError("ComfyUI did not queue the workflow. " + json.dumps(errors or queued)[:1200])
        deadline = time.time() + 600
        while time.time() < deadline:
            time.sleep(1.5)
            history = json.loads(request("/history/"+urllib.parse.quote(prompt_id), timeout=20).decode("utf-8"))
            entry = history.get(prompt_id)
            if not entry: continue
            status = entry.get("status", {})
            if status.get("status_str") == "error" or status.get("completed") is False and status.get("messages") and any(m and m[0] == "execution_error" for m in status.get("messages", [])):
                raise RuntimeError("ComfyUI workflow failed: " + json.dumps(status.get("messages", []))[:1200])
            for node in entry.get("outputs", {}).values():
                for item in node.get("images", []):
                    params = urllib.parse.urlencode({"filename":item.get("filename",""),"subfolder":item.get("subfolder",""),"type":item.get("type","output")})
                    image_bytes = request("/view?"+params, timeout=60)
                    if self.ai_options.get("second_pass") and not self.source_png:
                        image_bytes = self._run_comfyui_second_pass(image_bytes, request, checkpoint, prompt, gen_width, gen_height, client_id, time, uuid, json)
                    return image_bytes
        raise RuntimeError("ComfyUI generation timed out after 10 minutes. Check the ComfyUI console for the workflow status.")

    def _run_comfyui_second_pass(self, image_bytes, request, checkpoint, prompt, gen_width, gen_height, client_id, time, uuid, json):
        """Run an optional targeted img2img pass on the first generated image."""
        import urllib.parse
        boundary = "----NeuralSpriteRefine" + uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"neural-sprite-pass1.png\"\r\nContent-Type: image/png\r\n\r\n").encode() + image_bytes + f"\r\n--{boundary}--\r\n".encode()
        uploaded = json.loads(request("/upload/image", body, {"Content-Type":"multipart/form-data; boundary="+boundary}, timeout=60).decode())
        filename = uploaded.get("name")
        if not filename: raise RuntimeError("The second refinement pass could not upload the first generated image.")
        workflow = {
            "1":{"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":checkpoint}},
            "2":{"class_type":"LoadImage","inputs":{"image":filename}},
            "3":{"class_type":"VAEEncode","inputs":{"pixels":["2",0],"vae":["1",2]}},
            "4":{"class_type":"CLIPTextEncode","inputs":{"text":prompt + "\n\nPreserve the established composition and silhouette. Correct structural inconsistencies and clarify component separation without redesigning the subject.","clip":["1",1]}},
            "5":{"class_type":"CLIPTextEncode","inputs":{"text":self.ai_options.get("negative_prompt", "blurry, broken geometry, disconnected parts, text, watermark"),"clip":["1",1]}},
            "6":{"class_type":"KSampler","inputs":{"seed":int(time.time()*1000) % 2147483647,"steps":max(12,int(self.ai_options.get("steps",30)*0.7)),"cfg":float(self.ai_options.get("cfg",6.0)),"sampler_name":self.ai_options.get("sampler","euler_ancestral"),"scheduler":self.ai_options.get("scheduler","normal"),"denoise":float(self.ai_options.get("refine_strength",0.28)),"model":["1",0],"positive":["4",0],"negative":["5",0],"latent_image":["3",0]}},
            "7":{"class_type":"VAEDecode","inputs":{"samples":["6",0],"vae":["1",2]}},
            "8":{"class_type":"SaveImage","inputs":{"filename_prefix":"NeuralSprite_refined","images":["7",0]}}
        }
        queued=json.loads(request("/prompt",json.dumps({"prompt":workflow,"client_id":client_id}).encode(),{"Content-Type":"application/json"},timeout=30).decode())
        prompt_id=queued.get("prompt_id")
        if not prompt_id: raise RuntimeError("ComfyUI rejected the optional refinement pass: "+json.dumps(queued)[:900])
        deadline=time.time()+600
        while time.time()<deadline:
            time.sleep(1.5)
            history=json.loads(request("/history/"+urllib.parse.quote(prompt_id),timeout=20).decode())
            entry=history.get(prompt_id)
            if not entry: continue
            status=entry.get("status",{})
            if status.get("status_str")=="error": raise RuntimeError("ComfyUI refinement pass failed: "+json.dumps(status.get("messages",[]))[:900])
            for node in entry.get("outputs",{}).values():
                for item in node.get("images",[]):
                    params=urllib.parse.urlencode({"filename":item.get("filename",""),"subfolder":item.get("subfolder",""),"type":item.get("type","output")})
                    return request("/view?"+params,timeout=60)
        raise RuntimeError("ComfyUI refinement pass timed out after 10 minutes.")


class AIPromptDialog(QDialog):
    def __init__(self, operation, canvas_size, provider="OpenAI Cloud", parent=None):
        super().__init__(parent)
        self.operation, self.provider, self.canvas_size = operation, provider, canvas_size
        self.setWindowTitle("Generate Sprite" if operation == "generate" else "Refine Sprite")
        self.resize(600, 760 if provider == "Local ComfyUI" else 400)
        layout = QVBoxLayout(self)
        info_text = ("Choose final sprite dimensions and AI quality settings. The result opens in a separate tab; your current project remains unchanged." if operation == "generate" else
                     f"The current merged artwork ({canvas_size[0]} × {canvas_size[1]} px) will be used as a reference. The refined result opens in a separate tab.")
        info=QLabel(info_text); info.setWordWrap(True); layout.addWidget(info)
        self.width_spin=self.height_spin=None
        if operation == "generate":
            size_form=QFormLayout()
            self.width_spin=QSpinBox(); self.width_spin.setRange(1,4096); self.width_spin.setValue(canvas_size[0]); self.width_spin.setSuffix(" px")
            self.height_spin=QSpinBox(); self.height_spin.setRange(1,4096); self.height_spin.setValue(canvas_size[1]); self.height_spin.setSuffix(" px")
            size_form.addRow("Final sprite width:",self.width_spin); size_form.addRow("Final sprite height:",self.height_spin); layout.addLayout(size_form)
        self.options={}
        if provider == "Local ComfyUI":
            form=QFormLayout()
            self.quality=QComboBox(); self.quality.addItems(["Fast","Balanced","High Detail","Custom"]); self.quality.setCurrentText("Balanced")
            self.steps=QSpinBox(); self.steps.setRange(4,100); self.steps.setValue(30)
            self.cfg=QDoubleSpinBox(); self.cfg.setRange(1.0,20.0); self.cfg.setSingleStep(0.5); self.cfg.setDecimals(1); self.cfg.setValue(6.0)
            self.sampler=QComboBox(); self.sampler.addItems(["euler_ancestral","euler","dpmpp_2m","dpmpp_2m_sde","heun"])
            self.scheduler=QComboBox(); self.scheduler.addItems(["normal","karras","exponential","simple"])
            self.working_edge=QComboBox(); self.working_edge.addItems(["512","768","1024","1280","1536"]); self.working_edge.setCurrentText("1024")
            self.second_pass=QCheckBox("Run a second image-refinement pass"); self.second_pass.setChecked(False)
            self.refine_strength=QDoubleSpinBox(); self.refine_strength.setRange(0.10,0.80); self.refine_strength.setSingleStep(0.05); self.refine_strength.setDecimals(2); self.refine_strength.setValue(0.28)
            self.mechanical=QCheckBox("Add mechanical-structure guidance (tracks, wheels, connected parts)"); self.mechanical.setChecked(False)
            self.negative=QLineEdit("blurry, broken geometry, disconnected parts, floating components, extra limbs, text, watermark")
            form.addRow("Generation quality:",self.quality); form.addRow("Sampling steps:",self.steps); form.addRow("CFG scale:",self.cfg)
            form.addRow("Sampler:",self.sampler); form.addRow("Scheduler:",self.scheduler); form.addRow("Working resolution (long edge):",self.working_edge)
            form.addRow(self.second_pass); form.addRow("Second-pass strength:",self.refine_strength); form.addRow(self.mechanical); form.addRow("Negative prompt:",self.negative)
            layout.addWidget(QLabel("ComfyUI quality controls")); layout.addLayout(form)
            self.quality.currentTextChanged.connect(self._apply_preset)
            self.second_pass.setEnabled(operation == "generate")
            if operation != "generate": self.second_pass.setToolTip("The Refine operation already uses an image-to-image pass.")
            self.second_pass.toggled.connect(self.refine_strength.setEnabled)
            self.refine_strength.setEnabled(False)
            note=QLabel("High Detail increases sampling effort and may use more VRAM. A second pass can improve structure but may also alter details. Model support varies by checkpoint.")
            note.setWordWrap(True); note.setStyleSheet("color:#b7bec8;"); layout.addWidget(note)
        else:
            self.quality=self.steps=self.cfg=self.sampler=self.scheduler=self.working_edge=None
            self.second_pass=self.refine_strength=self.mechanical=self.negative=None
        layout.addWidget(QLabel("Describe the sprite or changes you want:"))
        self.prompt=QLineEdit(); self.prompt.setPlaceholderText("e.g. a WWII tank in strict side view with coherent track geometry"); layout.addWidget(self.prompt)
        buttons=QDialogButtonBox(QDialogButtonBox.Cancel)
        self.go=buttons.addButton("Generate in New Tab" if operation=="generate" else "Refine in New Tab",QDialogButtonBox.AcceptRole)
        self.go.clicked.connect(self._accept_if_valid); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def _apply_preset(self, preset):
        presets={"Fast":(18,6.0,768),"Balanced":(30,6.0,1024),"High Detail":(45,6.0,1280)}
        if preset not in presets: return
        steps,cfg,edge=presets[preset]
        for widget in (self.steps,self.cfg,self.working_edge): widget.blockSignals(True)
        self.steps.setValue(steps); self.cfg.setValue(cfg); self.working_edge.setCurrentText(str(edge))
        for widget in (self.steps,self.cfg,self.working_edge): widget.blockSignals(False)

    def _accept_if_valid(self):
        if self.operation=="generate" and self.provider=="Local ComfyUI":
            w,h=self.dimensions()
            if w<1 or h<1:
                QMessageBox.warning(self,"Unsupported dimensions","Width and height must be positive."); return
        self.accept()

    def prompt_text(self): return self.prompt.text().strip()
    def dimensions(self):
        if self.operation=="generate" and self.width_spin is not None: return self.width_spin.value(),self.height_spin.value()
        return self.canvas_size

    def ai_options(self):
        if self.provider != "Local ComfyUI": return {}
        return {"quality":self.quality.currentText(),"steps":self.steps.value(),"cfg":self.cfg.value(),
                "sampler":self.sampler.currentText(),"scheduler":self.scheduler.currentText(),"working_edge":int(self.working_edge.currentText()),
                "second_pass":self.second_pass.isChecked(),"refine_strength":self.refine_strength.value(),
                "mechanical_guidance":self.mechanical.isChecked(),"negative_prompt":self.negative.text().strip()}

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle(APP_NAME+" — Sprite Editor"); self.resize(1280,820)
        self.current_path=None
        self.settings=QSettings("NeuralSprite", "NeuralSprite")
        self.default_save_dir=self.settings.value("default_save_dir", "", type=str)
        self.project_tabs=QTabWidget()
        self.project_tabs.setTabsClosable(True)
        self.project_tabs.setMovable(True)
        self.project_tabs.currentChanged.connect(self._active_tab_changed)
        self.project_tabs.tabCloseRequested.connect(self.close_project_tab)
        self.setCentralWidget(self.project_tabs)
        self.canvas=PixelCanvas()
        self.project_tabs.addTab(self.canvas, "Untitled")
        self.statusBar().showMessage("Ready")
        self.canvas.on_status=self.statusBar().showMessage
        self.canvas.on_layers_changed=self.refresh_layers
        self.colors=ColorPanel(self.canvas); self.layers=LayersPanel(self.canvas)
        self.canvas.on_color_picked=self.colors.set_color
        self._build_tool_panel()
        self.colors.setMinimumWidth(235); self.colors.setMaximumWidth(360)
        self.layers.setMinimumWidth(235); self.layers.setMaximumWidth(360)
        self.color_dock=self._dock("Colors",self.colors,Qt.RightDockWidgetArea)
        self.layers_dock=self._dock("Layers",self.layers,Qt.RightDockWidgetArea)
        self.splitDockWidget(self.color_dock,self.layers_dock,Qt.Vertical)
        self.resizeDocks([self.color_dock,self.layers_dock],[470,300],Qt.Vertical)
        self._build_toolbar(); self._build_menus()
        self.refresh_layers()
        QTimer.singleShot(0, self.startup_flow)
    def _active_tab_changed(self, index):
        if index < 0:
            return
        canvas = self.project_tabs.widget(index)
        if not isinstance(canvas, PixelCanvas):
            return
        self.canvas = canvas
        self.current_path = getattr(canvas, "project_path", None)
        if hasattr(self, "colors"):
            self.colors.canvas = canvas
            self.layers.canvas = canvas
            canvas.on_color_picked = self.colors.set_color
            canvas.on_layers_changed = self.refresh_layers
            canvas.on_status = self.statusBar().showMessage
            self.colors.set_color(canvas.color)
            self.refresh_layers()
        self._sync_zoom_combo()

    def _sync_zoom_combo(self):
        if hasattr(self, "zoom_combo"):
            self.zoom_combo.blockSignals(True)
            self.zoom_combo.setCurrentText(f"{round(self.canvas.zoom / 12 * 100)}%")
            self.zoom_combo.blockSignals(False)

    def _sync_zoom_combo_for(self, canvas, value):
        if canvas is self.canvas and hasattr(self, "zoom_combo"):
            self.zoom_combo.blockSignals(True)
            self.zoom_combo.setCurrentText(f"{round(value / 12 * 100)}%")
            self.zoom_combo.blockSignals(False)

    def _add_project_tab(self, canvas, title, path=None, fit=False):
        canvas.project_path = path
        canvas.on_status = self.statusBar().showMessage
        canvas.on_zoom_changed = lambda value, c=canvas: self._sync_zoom_combo_for(c, value)
        canvas.on_layers_changed = self.refresh_layers
        canvas.on_color_picked = self.colors.set_color
        index = self.project_tabs.addTab(canvas, title)
        self.project_tabs.setCurrentIndex(index)
        self.canvas = canvas
        self.current_path = path
        self.colors.canvas = canvas
        self.layers.canvas = canvas
        self.colors.set_color(canvas.color)
        self.refresh_layers()
        if fit:
            QTimer.singleShot(0, lambda c=canvas: self.fit_canvas(c))
        return canvas

    def fit_canvas(self, canvas=None):
        canvas = canvas or self.canvas
        available_w = max(1, canvas.width() - 48)
        available_h = max(1, canvas.height() - 48)
        zoom = min(48.0, available_w / max(1, canvas.canvas_w), available_h / max(1, canvas.canvas_h))
        zoom = max(0.15, zoom)
        canvas.pan_offset = QPoint(0, 0)
        canvas.set_zoom(zoom)
        if canvas is self.canvas:
            self._sync_zoom_combo()

    def close_project_tab(self, index):
        if self.project_tabs.count() <= 1:
            # Keep one usable blank project open.
            old = self.project_tabs.widget(index)
            self.project_tabs.removeTab(index)
            if old: old.deleteLater()
            canvas = PixelCanvas()
            self._add_project_tab(canvas, "Untitled", fit=True)
            return
        widget = self.project_tabs.widget(index)
        self.project_tabs.removeTab(index)
        if widget: widget.deleteLater()

    def _dock(self,title,widget,area):
        d=QDockWidget(title,self); d.setWidget(widget); d.setAllowedAreas(Qt.LeftDockWidgetArea|Qt.RightDockWidgetArea); self.addDockWidget(area,d); return d
    def _build_tool_panel(self):
        panel=QWidget(); layout=QVBoxLayout(panel); layout.setContentsMargins(4,4,4,4); layout.setSpacing(4)
        self.tool_buttons={}
        self.last_tool_by_group = {"Pencil":"Pencil", "Eraser":"Eraser", "Rectangle":"Rectangle", "Selection":"Select Rectangle", "Move":"Move"}
        self.tool_group_labels={"Pencil": "Pencil", "Eraser": "Eraser", "Rectangle": "Rectangle", "Selection": "Selection", "Move": "Move"}
        self.selected_tool_label = QLabel("Pencil")
        self.selected_tool_label.setAlignment(Qt.AlignCenter)
        self.selected_tool_label.setWordWrap(True)
        self.selected_tool_label.setStyleSheet("font-weight:600; padding:4px; color:#f2f3f5;")
        layout.addWidget(self.selected_tool_label)
        for group in ("Pencil", "Eraser", "Rectangle", "Selection", "Move"):
            b=QToolButton(); b.setIcon(self._tool_icon(group)); b.setIconSize(QSize(22,22))
            b.setToolButtonStyle(Qt.ToolButtonIconOnly); b.setCheckable(True); b.setFixedSize(42,38)
            b.setToolTip(self.tool_group_labels[group] + " (right-click for related tools)")
            b.clicked.connect(lambda checked=False,g=group:self.set_tool(self.group_current_tool(g)))
            b.setContextMenuPolicy(Qt.CustomContextMenu); b.customContextMenuRequested.connect(lambda pos,g=group,btn=b:self.tool_context_menu(g,btn.mapToGlobal(pos)))
            layout.addWidget(b,0,Qt.AlignHCenter); self.tool_buttons[group]=b
        layout.addStretch()
        self.set_tool("Pencil")
        for name,key in [("Pencil","P"),("Eraser","E"),("Line","L"),("Rectangle","R"),("Fill","F"),("Select Rectangle","M"),("Select Ellipse","Shift+M"),("Magic Wand","W"),("Ellipse","O"),("Triangle","T"),("Move","V")]:
            action=QAction(name,self); action.setShortcut(key); action.setShortcutContext(Qt.ApplicationShortcut)
            action.triggered.connect(lambda checked=False,n=name:self.set_tool(n)); self.addAction(action)
        d=QDockWidget("Tools",self); d.setWidget(panel); d.setAllowedAreas(Qt.LeftDockWidgetArea); self.addDockWidget(Qt.LeftDockWidgetArea,d)
    def group_current_tool(self, group):
        # A left-click always restores the last chosen tool for that tool group,
        # even after another group was used. Right-click changes this remembered choice.
        return self.last_tool_by_group.get(group, group)
    def _tool_icon(self, name):
        """Draw simple monochrome tool glyphs inspired by familiar raster-editor conventions."""
        pix = QPixmap(32, 32); pix.fill(Qt.transparent)
        q = QPainter(pix); q.setRenderHint(QPainter.Antialiasing, False)
        black = QPen(QColor("#111111"), 2, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin)
        white = QColor("#ffffff")
        q.setPen(black); q.setBrush(white)
        if name in ("Pencil", "Fill"):
            if name == "Pencil":
                q.save(); q.translate(16,16); q.rotate(-42); q.drawRect(QRect(-4,-11,8,20)); q.drawLine(-4,9,0,14); q.drawLine(4,9,0,14); q.restore()
            else:
                q.drawPolygon(QPolygon([QPoint(7,12),QPoint(13,6),QPoint(25,18),QPoint(19,24)])); q.drawLine(7,12,5,20); q.drawLine(5,20,13,18); q.drawArc(QRect(17,17,10,10),180*16,180*16)
        elif name == "Eraser":
            q.drawPolygon(QPolygon([QPoint(5,19),QPoint(16,6),QPoint(27,16),QPoint(17,27),QPoint(5,19)])); q.drawLine(11,22,21,22)
        elif name in ("Rectangle", "Ellipse", "Triangle", "Line"):
            if name == "Rectangle": q.drawRect(QRect(5,7,22,18))
            elif name == "Ellipse": q.drawEllipse(QRect(5,7,22,18))
            elif name == "Triangle": q.drawPolygon(QPolygon([QPoint(16,5),QPoint(27,26),QPoint(5,26)]))
            else: q.drawLine(5,26,27,6)
        elif name in ("Selection", "Select Rectangle", "Select Ellipse", "Magic Wand"):
            q.setPen(QPen(QColor("#111111"), 1, Qt.DotLine)); q.setBrush(Qt.NoBrush)
            if name == "Select Ellipse": q.drawEllipse(QRect(5,7,22,18))
            elif name == "Magic Wand":
                q.drawLine(8,24,22,10); q.drawLine(19,7,24,12); q.drawLine(23,5,25,3); q.drawLine(26,10,29,10); q.drawLine(24,15,26,17)
            else: q.drawRect(QRect(5,7,22,18))
        elif name == "Move":
            q.drawLine(16,3,16,29); q.drawLine(3,16,29,16)
            q.drawLine(16,3,12,8); q.drawLine(16,3,20,8); q.drawLine(16,29,12,24); q.drawLine(16,29,20,24)
            q.drawLine(3,16,8,12); q.drawLine(3,16,8,20); q.drawLine(29,16,24,12); q.drawLine(29,16,24,20)
        q.end(); return QIcon(pix)
    def tool_context_menu(self,group,pos):
        menu=QMenu(self)
        tools = ("Pencil", "Fill") if group == "Pencil" else ("Rectangle", "Triangle", "Ellipse", "Line") if group == "Rectangle" else ("Select Rectangle", "Select Ellipse", "Magic Wand") if group == "Selection" else ("Move",) if group == "Move" else ("Eraser",)
        display={"Fill":"Fill Bucket", "Select Rectangle":"Rectangle Selection", "Select Ellipse":"Ellipse Selection", "Magic Wand":"Magic Wand (Contiguous Color)"}
        for tool in tools:
            action=menu.addAction(display.get(tool,tool)); action.setCheckable(True); action.setChecked(self.canvas.tool==tool)
            action.triggered.connect(lambda checked=False,n=tool:self.set_tool(n))
        menu.exec(pos)
    def _build_toolbar(self):
        tb=QToolBar("Canvas Controls"); tb.setMovable(False); self.addToolBar(tb)
        brush_label=QLabel("Brush\nSize"); brush_label.setAlignment(Qt.AlignCenter); tb.addWidget(brush_label); brush=QSpinBox(); brush.setRange(1,32); brush.setValue(1); brush.valueChanged.connect(self.set_brush_size); tb.addWidget(brush); self.brush_spin=brush
        fill_shapes=QCheckBox("Fill Shapes"); fill_shapes.setChecked(True); fill_shapes.toggled.connect(self.set_fill_shapes); tb.addWidget(fill_shapes); self.fill_shapes_checkbox=fill_shapes
        tb.addSeparator(); tb.addWidget(QLabel(" Zoom "))
        zoom=QComboBox(); zoom.setEditable(True); zoom.addItems(["5%","10%","15%","20%","25%","33%","50%","66%","75%","100%","125%","150%","200%","300%","400%","600%","800%"]); zoom.setCurrentText("100%")
        zoom.currentTextChanged.connect(lambda v:self.canvas.set_zoom(max(0.15, min(48.0, int(v[:-1]) * 12 / 100))) if v.endswith("%") else None); tb.addWidget(zoom); self.zoom_combo=zoom
        grid=QAction("Grid",self); grid.setCheckable(True); grid.setChecked(True); grid.toggled.connect(self.toggle_grid); tb.addAction(grid)
        tb.addSeparator(); tb.addWidget(QLabel(" Symmetry "))
        symmetry=QComboBox(); symmetry.addItems(["Off", "Horizontal", "Vertical"]); symmetry.currentTextChanged.connect(self.set_symmetry); tb.addWidget(symmetry); self.symmetry_combo=symmetry
    def _build_menus(self):
        file=self.menuBar().addMenu("File")
        for text,fn,shortcut in [("New",self.new_project,"Ctrl+N"),("Open Project…",self.open_project,"Ctrl+O"),("Import PNG…",self.import_png,"Ctrl+Shift+I"),("Save Project",self.save_project,"Ctrl+S"),("Save Project As…",self.save_as,"Ctrl+Shift+S"),("Export PNG…",self.export_png,"Ctrl+E")]:
            a=QAction(text,self); a.setShortcut(shortcut); a.triggered.connect(fn); file.addAction(a)
        edit=self.menuBar().addMenu("Edit")
        for text,fn,shortcut in [("Undo",lambda:self.canvas.undo(),"Ctrl+Z"),("Redo",lambda:self.canvas.redo(),"Ctrl+Y")]:
            a=QAction(text,self); a.setShortcut(shortcut); a.setShortcutContext(Qt.ApplicationShortcut); a.triggered.connect(fn); edit.addAction(a)
        delete_action = QAction("Clear Selected Pixels", self)
        delete_action.setShortcuts(["Delete", "Backspace"])
        delete_action.setShortcutContext(Qt.ApplicationShortcut)
        delete_action.triggered.connect(self.delete_selected_pixels)
        self.addAction(delete_action); edit.addAction(delete_action)
        image=self.menuBar().addMenu("Image")
        resize=QAction("Resize Canvas…",self); resize.triggered.connect(self.resize_canvas); image.addAction(resize)
        view=self.menuBar().addMenu("View")
        settings=QAction("AI Provider Settings…",self); settings.triggered.connect(self.ai_settings); view.addAction(settings)
        shortcuts=QAction("Keyboard & Mouse Shortcuts…",self); shortcuts.triggered.connect(self.show_shortcuts); view.addAction(shortcuts)
        ai=self.menuBar().addMenu("AI")
        generate=QAction("Generate New Sprite…",self); generate.triggered.connect(self.ai_generate); ai.addAction(generate)
        refine=QAction("Refine Current Sprite…",self); refine.triggered.connect(self.ai_refine); ai.addAction(refine)
        ai.addSeparator()
        settings_action=QAction("AI Provider Settings…",self); settings_action.triggered.connect(self.ai_settings); ai.addAction(settings_action)
    def set_brush_size(self,value):
        self.canvas.brush_size=value; self.canvas.update()
    def set_fill_shapes(self, enabled):
        self.canvas.fill_shapes = enabled
        self.canvas._preview_cache = None
        self.canvas._preview_cache_key = None
        self.canvas.update()
    def set_tool(self,name):
        self.canvas.tool=name
        self.canvas._selection_outline_cache = None
        self.canvas._selection_outline_key = None
        group = "Pencil" if name in ("Pencil", "Fill") else "Rectangle" if name in ("Rectangle", "Ellipse", "Triangle", "Line") else "Selection" if name in ("Select Rectangle", "Select Ellipse", "Magic Wand") else "Move" if name == "Move" else "Eraser"
        labels={"Pencil":"Pencil", "Fill":"Fill Bucket", "Eraser":"Eraser", "Rectangle":"Rectangle", "Ellipse":"Ellipse", "Triangle":"Triangle", "Line":"Line", "Select Rectangle":"Rectangle Selection", "Select Ellipse":"Ellipse Selection", "Magic Wand":"Magic Wand", "Move":"Move"}
        self.last_tool_by_group[group] = name
        for n,b in getattr(self,"tool_buttons",{}).items():
            b.setChecked(n==group)
            b.setToolTip((labels[name] if n==group else self.tool_group_labels[n]) + " (right-click for related tools)")
            if n == group:
                b.setIcon(self._tool_icon(name))
        if hasattr(self, "selected_tool_label"):
            self.selected_tool_label.setText(labels[name])
        self.statusBar().showMessage("Tool: "+labels[name])
        self.canvas.update()
    def delete_selected_pixels(self):
        c = self.canvas
        if c.selection_rect is None:
            return
        c.snapshot()
        image = c.layers[c.active_layer].image
        r = c.selection_rect
        # Clear exact selected pixels individually. This avoids QPainter clip-path
        # antialiasing / focus problems and preserves all pixels outside the mask.
        for y in range(max(0, r.top()), min(c.canvas_h, r.bottom()+1)):
            for x in range(max(0, r.left()), min(c.canvas_w, r.right()+1)):
                if c._point_selected(QPoint(x,y)):
                    image.setPixelColor(x, y, QColor(0,0,0,0))
        c._changed()
        self.statusBar().showMessage("Cleared selected pixels", 2000)
    def set_symmetry(self, mode):
        self.canvas.symmetry = mode
        self.statusBar().showMessage("Symmetry: " + mode, 2500)
        self.canvas.update()
    def toggle_grid(self,on): self.canvas.show_grid=on; self.canvas.update()
    def refresh_layers(self): self.layers.refresh()
    def new_project(self, startup=False):
        dims=CanvasSizeDialog(32,32,self)
        if dims.exec()!=QDialog.Accepted:return
        w,h=dims.dimensions()
        canvas=PixelCanvas(); canvas.canvas_w=w; canvas.canvas_h=h
        canvas.layers=[ArtLayer("Layer 1",QImage(w,h,QImage.Format_ARGB32))]
        canvas.layers[0].image.fill(Qt.transparent)
        canvas._merged_cache=None; canvas._checker_cache=None
        self._add_project_tab(canvas, "Untitled", path=None, fit=True)

    def _ensure_default_save_dir(self):
        if self.default_save_dir and os.path.isdir(self.default_save_dir):
            return True
        documents=QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) or os.path.expanduser("~")
        suggested=os.path.join(documents, "Neural Sprite")
        chosen=QFileDialog.getExistingDirectory(self, "Choose Neural Sprite default save location", suggested, QFileDialog.ShowDirsOnly)
        if not chosen:
            # Provide a reliable default if the user cancels the first-run picker.
            chosen=suggested
        os.makedirs(chosen, exist_ok=True)
        self.default_save_dir=chosen
        self.settings.setValue("default_save_dir", chosen)
        return True

    def _dialog_start_path(self):
        if self.current_path:
            return self.current_path
        self._ensure_default_save_dir()
        return self.default_save_dir or os.path.expanduser("~")

    def startup_flow(self):
        self._ensure_default_save_dir()
        dialog=QDialog(self); dialog.setWindowTitle("Welcome to Neural Sprite"); dialog.setMinimumWidth(420)
        layout=QVBoxLayout(dialog)
        title=QLabel("Welcome to Neural Sprite")
        title.setStyleSheet("font-size:18px; font-weight:600; padding:6px;")
        layout.addWidget(title)
        folder=QLabel("Default save location:\n" + (self.default_save_dir or "Not set")); folder.setWordWrap(True)
        layout.addWidget(folder)
        buttons=QHBoxLayout()
        new_btn=QPushButton("New Project")
        open_btn=QPushButton("Open Existing Project…")
        buttons.addWidget(new_btn); buttons.addWidget(open_btn); layout.addLayout(buttons)
        recent=QListWidget(); recent.setMaximumHeight(150)
        try:
            files=sorted([f for f in os.listdir(self.default_save_dir) if f.lower().endswith(".pfa")], key=lambda f: os.path.getmtime(os.path.join(self.default_save_dir,f)), reverse=True)[:8]
        except OSError: files=[]
        for name in files: recent.addItem(name)
        if files:
            layout.addWidget(QLabel("Recent projects in the default save location:")); layout.addWidget(recent)
        footer=QHBoxLayout(); change=QPushButton("Change Save Location…"); cancel=QPushButton("Exit")
        footer.addWidget(change); footer.addStretch(); footer.addWidget(cancel); layout.addLayout(footer)
        outcome={"action":None}
        def choose_new(): outcome["action"]="new"; dialog.accept()
        def choose_open(): outcome["action"]="open"; dialog.accept()
        def choose_recent():
            item=recent.currentItem()
            if item:
                outcome["action"]="recent"; outcome["path"]=os.path.join(self.default_save_dir,item.text()); dialog.accept()
        def change_folder():
            chosen=QFileDialog.getExistingDirectory(dialog,"Choose default save location",self.default_save_dir or os.path.expanduser("~"),QFileDialog.ShowDirsOnly)
            if chosen:
                os.makedirs(chosen,exist_ok=True); self.default_save_dir=chosen; self.settings.setValue("default_save_dir",chosen); folder.setText("Default save location:\n"+chosen)
                recent.clear()
                try:
                    for name in sorted([f for f in os.listdir(chosen) if f.lower().endswith(".pfa")],key=lambda f:os.path.getmtime(os.path.join(chosen,f)),reverse=True)[:8]: recent.addItem(name)
                except OSError: pass
        new_btn.clicked.connect(choose_new); open_btn.clicked.connect(choose_open); recent.itemDoubleClicked.connect(lambda item:choose_recent()); change.clicked.connect(change_folder); cancel.clicked.connect(dialog.reject)
        if dialog.exec()!=QDialog.Accepted:
            self.close(); return
        if outcome["action"] in ("new", "open", "recent") and self.project_tabs.count() == 1:
            placeholder = self.project_tabs.widget(0)
            if getattr(placeholder, "project_path", None) is None and self.project_tabs.tabText(0) == "Untitled":
                self.project_tabs.removeTab(0)
                placeholder.deleteLater()
        if outcome["action"]=="new": self.new_project(startup=True)
        elif outcome["action"]=="open": self.open_project()
        elif outcome["action"]=="recent": self.open_project_path(outcome["path"])
        else: self.close()

    def save_project(self):
        if not self.current_path: return self.save_as()
        self._write_project(self.current_path)
    def save_as(self):
        path,_=QFileDialog.getSaveFileName(self,"Save Neural Sprite Project",self._dialog_start_path(),"Neural Sprite Project (*.pfa)")
        if path:
            if not path.lower().endswith(".pfa"): path += ".pfa"
            self.current_path=path; self._write_project(path)
    def _write_project(self,path):
        import base64
        data={"format":"PixelForgeAI","version":1,"width":self.canvas.canvas_w,"height":self.canvas.canvas_h,"active_layer":self.canvas.active_layer,"layers":[]}
        for layer in self.canvas.layers:
            ba=QByteArray()
            buffer=QBuffer(ba)
            buffer.open(QBuffer.WriteOnly)
            layer.image.save(buffer,"PNG")
            buffer.close()
            data["layers"].append({"name":layer.name,"visible":layer.visible,"opacity":layer.opacity,"png":base64.b64encode(bytes(ba)).decode("ascii")})
        with open(path,"w",encoding="utf-8") as f: json.dump(data,f)
        self.current_path = path
        self.canvas.project_path = path
        index = self.project_tabs.indexOf(self.canvas)
        if index >= 0: self.project_tabs.setTabText(index, os.path.basename(path))
        self.statusBar().showMessage("Saved "+path,4000)
    def open_project(self):
        path,_=QFileDialog.getOpenFileName(self,"Open Project",self._dialog_start_path(),"Neural Sprite Project (*.pfa)")
        if path: self.open_project_path(path)

    def open_project_path(self,path):
        if not path:return
        try:
            import base64
            with open(path,encoding="utf-8") as f: data=json.load(f)
            if data.get("format")!="PixelForgeAI": raise ValueError("Not a supported Neural Sprite project")
            c=PixelCanvas(); c.canvas_w=data["width"]; c.canvas_h=data["height"]; c.layers=[]
            for item in data["layers"]:
                img=QImage.fromData(base64.b64decode(item["png"]),"PNG")
                c.layers.append(ArtLayer(item["name"],img,item.get("visible",True),item.get("opacity",255)))
            c.active_layer=min(data.get("active_layer",0),len(c.layers)-1)
            title=os.path.basename(path)
            # Avoid opening the same project twice in separate tabs.
            for i in range(self.project_tabs.count()):
                existing=self.project_tabs.widget(i)
                if getattr(existing, "project_path", None) == path:
                    self.project_tabs.setCurrentIndex(i)
                    return
            self._add_project_tab(c, title, path=path, fit=True)
            c.update()
        except Exception as e: QMessageBox.critical(self,"Open failed",str(e))
    def import_png(self):
        """Import a PNG as a new editable project tab at its original pixel dimensions."""
        path,_=QFileDialog.getOpenFileName(
            self, "Import PNG as New Project", self._dialog_start_path(), "PNG Images (*.png)"
        )
        if not path:
            return
        image=QImage(path)
        if image.isNull():
            QMessageBox.warning(self, "Import PNG Failed", "The selected file could not be opened as a valid image.")
            return
        # Keep the PNG's exact dimensions and alpha channel; do not rescale during import.
        image=image.convertToFormat(QImage.Format_ARGB32)
        canvas=PixelCanvas()
        canvas.canvas_w=image.width()
        canvas.canvas_h=image.height()
        canvas.layers=[ArtLayer("Imported PNG", image)]
        canvas.active_layer=0
        canvas._merged_cache=None
        canvas._checker_cache=None
        canvas.undo_stack=[]
        canvas.redo_stack=[]
        self._add_project_tab(canvas, os.path.basename(path), path=None, fit=True)
        self.statusBar().showMessage(
            f"Imported {os.path.basename(path)} ({image.width()} × {image.height()} px) into a new project tab", 5000
        )

    def export_png(self):
        path,_=QFileDialog.getSaveFileName(self,"Export PNG",self._dialog_start_path(),"PNG Image (*.png)")
        if path:
            if not path.lower().endswith(".png"): path += ".png"
            self.canvas.merged_image().save(path,"PNG"); self.statusBar().showMessage("Exported "+path,4000)
    def resize_canvas(self):
        dims=CanvasSizeDialog(self.canvas.canvas_w,self.canvas.canvas_h,self)
        dims.setWindowTitle("Resize Canvas")
        if dims.exec()==QDialog.Accepted:
            w,h=dims.dimensions(); self.canvas.resize_canvas(w,h)
    def show_shortcuts(self):
        QMessageBox.information(self,"Keyboard & Mouse Shortcuts",
            "TOOLS\nP  Pencil\nE  Eraser\nF  Fill Bucket (right-click Pencil)\nL  Line\nR  Rectangle (right-click for other shapes)\nO  Ellipse\nT  Triangle\nM  Rectangle Selection (Shift+M for ellipse selection)\nW  Magic Wand (contiguous exact-color selection)\nV  Move selected pixels\nCtrl while drawing shapes: constrain square/circle; Alt: draw from center\nFill Shapes checkbox: toggle filled vs outline shapes\nRight-click Rectangle for Rectangle/Triangle/Ellipse/Line\n\nPROJECT\nCtrl+N  New canvas\nCtrl+O  Open project\nCtrl+S  Save project\nCtrl+Shift+S  Save as\nCtrl+E  Export PNG\nCtrl+Z / Ctrl+Y  Undo / redo\n[ / ]  Decrease / increase brush size\n+ / -  Zoom in / out\nG  Toggle grid\n\nMOUSE\nWheel  Zoom in/out\nAlt+Wheel  Pan left/right\nCtrl+Wheel  Pan up/down\nMiddle-click + drag  Pan canvas")
    def keyPressEvent(self,event):
        if event.key() == Qt.Key_Escape:
            self.canvas.deselect(); return
        if event.key() == Qt.Key_G and not (event.modifiers() & (Qt.ControlModifier|Qt.AltModifier)):
            self.toggle_grid(not self.canvas.show_grid); return
        if event.key() in (Qt.Key_BracketLeft, Qt.Key_BracketRight):
            self.canvas.brush_size=max(1,min(12,self.canvas.brush_size + (1 if event.key()==Qt.Key_BracketRight else -1)))
            self.statusBar().showMessage(f"Brush size: {self.canvas.brush_size}"); return
        if event.key() in (Qt.Key_Plus, Qt.Key_Equal, Qt.Key_Minus):
            self.canvas.set_zoom(max(0.15,min(48,self.canvas.zoom * (1.1 if event.key() in (Qt.Key_Plus,Qt.Key_Equal) else 1/1.1)))); return
        super().keyPressEvent(event)
    def ai_settings(self):
        SettingsDialog(self).exec()

    def _start_ai_task(self, operation):
        settings = QSettings("NeuralSprite", "NeuralSprite")
        legacy = QSettings("PixelForgeAI", "PixelForgeAI")
        if not settings.contains("ai/provider") and legacy.contains("ai/provider"):
            for key in ("ai/provider", "ai/openai_api_key", "ai/openai_model", "ai/local_endpoint", "ai/local_checkpoint"):
                if legacy.contains(key): settings.setValue(key, legacy.value(key))
        provider = settings.value("ai/provider", "OpenAI Cloud", type=str)
        api_key = settings.value("ai/openai_api_key", "", type=str).strip()
        if provider != "Local ComfyUI" and not api_key:
            QMessageBox.information(self, "Configure AI", "Add your OpenAI API key or select Local ComfyUI under View → AI Provider Settings.")
            self.ai_settings()
            provider = settings.value("ai/provider", "OpenAI Cloud", type=str)
            api_key = settings.value("ai/openai_api_key", "", type=str).strip()
            if provider != "Local ComfyUI" and not api_key: return
        dialog = AIPromptDialog(operation, (self.canvas.canvas_w, self.canvas.canvas_h), provider, self)
        if dialog.exec() != QDialog.Accepted: return
        prompt = dialog.prompt_text()
        if not prompt:
            QMessageBox.warning(self, "Prompt required", "Enter a short description before starting generation.")
            return
        source_png = b""
        if operation == "refine":
            buffer_array = QByteArray(); buffer = QBuffer(buffer_array); buffer.open(QBuffer.WriteOnly)
            self.canvas.merged_image().save(buffer, "PNG"); buffer.close(); source_png = bytes(buffer_array)
        model = settings.value("ai/openai_model", "gpt-image-2.5-sunburst", type=str)
        endpoint = settings.value("ai/local_endpoint", "http://127.0.0.1:8188", type=str).strip().rstrip("/")
        checkpoint = settings.value("ai/local_checkpoint", "", type=str)
        target_size = dialog.dimensions()
        ai_options = dialog.ai_options()
        self._ai_progress = QDialog(self); self._ai_progress.setWindowTitle("Neural Sprite")
        self._ai_progress.setModal(False); self._ai_progress.setMinimumWidth(360)
        progress_layout = QVBoxLayout(self._ai_progress)
        provider_label = "Local ComfyUI" if provider == "Local ComfyUI" else "OpenAI"
        message = QLabel(f"Generating with {provider_label}…\nThis may take a while. Your current project remains available and unchanged.")
        message.setWordWrap(True); progress_layout.addWidget(message)
        self._ai_progress.setWindowFlag(Qt.WindowCloseButtonHint, False); self._ai_progress.show()
        self.statusBar().showMessage(f"AI image request in progress ({provider_label})…")
        self._ai_worker = AIImageWorker(provider, api_key, model, endpoint, checkpoint, prompt, source_png, operation, target_size, ai_options, self)
        self._ai_worker.succeeded.connect(self._ai_succeeded); self._ai_worker.failed.connect(self._ai_failed); self._ai_worker.start()

    def ai_generate(self):
        self._start_ai_task("generate")

    def ai_refine(self):
        self._start_ai_task("refine")

    def _ai_succeeded(self, image, operation, diagnostics=""):
        if hasattr(self, "_ai_progress") and self._ai_progress:
            self._ai_progress.close()
            self._ai_progress.deleteLater()
        canvas = PixelCanvas()
        canvas.canvas_w, canvas.canvas_h = image.width(), image.height()
        canvas.layers = [ArtLayer("AI Generated", image)]
        canvas.active_layer = 0
        canvas._merged_cache = None
        canvas._checker_cache = None
        canvas.project_path = None
        title = "AI Generated" if operation == "generate" else "AI Refined"
        self._add_project_tab(canvas, title, path=None, fit=True)
        self.statusBar().showMessage(f"{title} sprite opened in a new project tab", 6000)
        QMessageBox.information(self, "AI complete", f"The result is open in a new '{title}' tab. Your original project was not changed.\n\n{diagnostics}\n\nUse Ctrl+Shift+S to save the new project.")

    def _ai_failed(self, message):
        if hasattr(self, "_ai_progress") and self._ai_progress:
            self._ai_progress.close()
            self._ai_progress.deleteLater()
        self.statusBar().showMessage("AI request failed", 5000)
        QMessageBox.critical(self, "AI request failed", message + "\n\nFor OpenAI, check your API key, API credits, network connection, and model. For local generation, check the ComfyUI URL, installed checkpoint, and ComfyUI console.")

# Qt buffer imports kept here for straightforward project serialization.
from PySide6.QtCore import QByteArray, QBuffer, QIODevice

def main():
    app=QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet("""
      QMainWindow, QWidget { background:#25282d; color:#e4e7eb; }
      QDockWidget::title { background:#30343b; padding:7px; }
      QToolBar { background:#30343b; spacing:4px; padding:4px; }
      QPushButton, QComboBox, QSpinBox, QLineEdit, QListWidget { background:#343940; color:#f2f3f5; border:1px solid #505660; padding:5px; }
      QPushButton:hover { background:#454b55; }
      QMenuBar, QMenu { background:#30343b; color:#f2f3f5; }
      QMenu::item:selected { background:#505966; }
      QStatusBar { background:#30343b; }
    """)
    w=MainWindow(); w.show(); sys.exit(app.exec())
if __name__=="__main__": main()
