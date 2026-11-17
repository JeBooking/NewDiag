"""MVC 的 Controller：把 Model 和 View 接起来，桌面窗口只跟它打交道。"""

__all__ = ['Controller']


class Controller:
    def __init__(self, model, view, sensor):
        self.model = model
        self.view = view
        self.sensor = sensor

    def reload(self, points: int = 40, window: int = 3) -> dict:
        raw = self.sensor.read(points)
        self.model.load(raw)
        series = self.model.smooth(raw, window)
        return {'series': series, 'summary': self.model.summary(series)}
