class Normalizer:
    def __init__(self, *args, **kwargs):
        pass

    def normalize(self, text: str) -> str:
        return text

    def __call__(self, text: str) -> str:
        return self.normalize(text)

